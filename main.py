from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

import auth
import db
import moon_calendar as mc


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    yield

app = FastAPI(title="bouj moon calendar", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── response models ──────────────────────────────────────────────────────────

class LunarReading(BaseModel):
    month: int
    is_leap: bool
    day: int
    display: str


class GateDay(BaseModel):
    new_moon_at: str
    closing: LunarReading
    opening: LunarReading


class Phase(BaseModel):
    name: str
    at: str


class Phases(BaseModel):
    recent: Optional[Phase]
    upcoming: Optional[Phase]


class MoonResponse(BaseModel):
    date: str
    is_gate_day: bool
    reading: Optional[LunarReading] = None
    gate: Optional[GateDay] = None
    phases: Optional[Phases] = None


class AuditMonth(BaseModel):
    month: int
    is_leap: bool
    display: str
    start_utc: str
    end_utc: str
    zhongqi: Optional[dict] = None
    is_current: bool


class AuditResponse(BaseModel):
    sui_start_utc: str
    sui_end_utc: str
    month_count: int
    months: list[AuditMonth]


class AuthRequest(BaseModel):
    password: str


class AuthResponse(BaseModel):
    token: str
    username: str


class MeResponse(BaseModel):
    username: str


class PostResponse(BaseModel):
    id: int
    title: str
    body: str
    author: str
    created_at: datetime
    updated_at: Optional[datetime] = None


class PostCreate(BaseModel):
    title: str
    body: str


class PostUpdate(BaseModel):
    title: Optional[str] = None
    body: Optional[str] = None


# ── helpers ──────────────────────────────────────────────────────────────────

def _reading(month: int, is_leap: bool, day: int) -> LunarReading:
    display = ("leap " if is_leap else "") + f"{mc.month_to_words(month)} moon day {mc.day_to_words(day)}"
    return LunarReading(month=month, is_leap=is_leap, day=day, display=display)


def _phases_near(now: datetime, tz) -> Phases:
    utc_now = now.astimezone(timezone.utc)
    t = mc._time_from_dt(utc_now)
    window_start_tt = t.tt - 36 / 24.0
    all_phases = mc._major_phases_near(t)

    recent_pair = next(
        ((time, name) for time, name in reversed(all_phases)
         if window_start_tt <= time.tt <= t.tt),
        None,
    )
    upcoming_pair = next(
        ((time, name) for time, name in all_phases if time.tt > t.tt),
        None,
    )

    recent = Phase(name=recent_pair[1], at=mc._dt_from_time(recent_pair[0], tz).isoformat()) if recent_pair else None
    upcoming = Phase(name=upcoming_pair[1], at=mc._dt_from_time(upcoming_pair[0], tz).isoformat()) if upcoming_pair else None
    return Phases(recent=recent, upcoming=upcoming)


# ── endpoints ────────────────────────────────────────────────────────────────

@app.get("/moon", response_model=MoonResponse)
def get_moon(
    lat:     float          = Query(..., ge=-90,  le=90),
    lon:     float          = Query(..., ge=-180, le=180),
    date:    Optional[str]  = Query(None, description="YYYY-MM-DD; omit for today"),
    phases:  bool           = Query(False),
):
    tz = mc._location_tz(lat, lon)

    if date:
        try:
            date_obj = datetime.strptime(date, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(400, "date must be YYYY-MM-DD")
        local_dt = mc._localize_noon(date_obj, tz)
    else:
        local_dt = datetime.now(tz)
        date_obj = local_dt.date()

    phases_data = _phases_near(local_dt, tz) if phases else None

    nm = mc._gate_new_moon(date_obj, tz)
    if nm is not None:
        nm_local = mc._dt_from_time(nm, tz)
        (m_b, l_b, d_b), (m_a, l_a, d_a) = mc.gate_readings(nm, tz)
        return MoonResponse(
            date=date_obj.isoformat(),
            is_gate_day=True,
            gate=GateDay(
                new_moon_at=nm_local.isoformat(),
                closing=_reading(m_b, l_b, d_b),
                opening=_reading(m_a, l_a, d_a),
            ),
            phases=phases_data,
        )

    month_n, is_leap, day_n = mc.lunar_date(local_dt)
    return MoonResponse(
        date=date_obj.isoformat(),
        is_gate_day=False,
        reading=_reading(month_n, is_leap, day_n),
        phases=phases_data,
    )


@app.get("/moon/audit", response_model=AuditResponse)
def get_audit(
    lat: float = Query(..., ge=-90,  le=90),
    lon: float = Query(..., ge=-180, le=180),
):
    tz = mc._location_tz(lat, lon)
    now = datetime.now(tz)
    utc_dt = now.astimezone(timezone.utc)
    t = mc._time_from_dt(utc_dt)
    prev_new = mc._previous_new_moon(t)
    sui, assignments = mc._locate_sui(prev_new, utc_dt.year)

    months = []
    for (nm_start, nm_end, month_num, is_leap), (_, _, zhongqi) in zip(assignments, sui):
        months.append(AuditMonth(
            month=month_num,
            is_leap=is_leap,
            display=("leap " if is_leap else "") + mc.month_to_words(month_num) + " moon",
            start_utc=mc._dt_from_time(nm_start, timezone.utc).strftime("%Y-%m-%d"),
            end_utc=mc._dt_from_time(nm_end, timezone.utc).strftime("%Y-%m-%d"),
            zhongqi={"longitude": zhongqi[0], "name": zhongqi[1]} if zhongqi else None,
            is_current=abs(nm_start.tt - prev_new.tt) < 0.01,
        ))

    return AuditResponse(
        sui_start_utc=mc._dt_from_time(assignments[0][0], timezone.utc).isoformat(),
        sui_end_utc=mc._dt_from_time(assignments[-1][1], timezone.utc).isoformat(),
        month_count=len(sui),
        months=months,
    )


# ── auth ─────────────────────────────────────────────────────────────────────

_bearer = HTTPBearer()


@app.post("/auth", response_model=AuthResponse)
def post_auth(body: AuthRequest):
    username = auth.authenticate(body.password)
    if not username:
        raise HTTPException(status_code=401, detail="invalid password")
    return AuthResponse(token=auth.create_token(username), username=username)


@app.get("/me", response_model=MeResponse)
def get_me(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    try:
        username = auth.decode_token(creds.credentials)
    except Exception:
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return MeResponse(username=username)


# ── posts ─────────────────────────────────────────────────────────────────────

_WRITERS = {"admin"}


def _require_auth(creds: HTTPAuthorizationCredentials) -> str:
    try:
        return auth.decode_token(creds.credentials)
    except Exception:
        raise HTTPException(status_code=401, detail="invalid or expired token")


@app.get("/posts", response_model=list[PostResponse])
def list_posts(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    _require_auth(creds)
    return db.get_posts()


@app.post("/posts", response_model=PostResponse, status_code=201)
def create_post(data: PostCreate, creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    username = _require_auth(creds)
    if username not in _WRITERS:
        raise HTTPException(status_code=403, detail="not authorized to write posts")
    return db.create_post(data.title, data.body, username)


@app.patch("/posts/{post_id}", response_model=PostResponse)
def update_post(post_id: int, data: PostUpdate, creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    username = _require_auth(creds)
    if username not in _WRITERS:
        raise HTTPException(status_code=403, detail="not authorized to edit posts")
    existing = db.get_post(post_id)
    if not existing:
        raise HTTPException(status_code=404, detail="post not found")
    return db.update_post(
        post_id,
        data.title if data.title is not None else existing["title"],
        data.body  if data.body  is not None else existing["body"],
    )


@app.delete("/posts/{post_id}", status_code=204)
def delete_post(post_id: int, creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    username = _require_auth(creds)
    if username not in _WRITERS:
        raise HTTPException(status_code=403, detail="not authorized to delete posts")
    if not db.delete_post(post_id):
        raise HTTPException(status_code=404, detail="post not found")
