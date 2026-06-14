from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from pydantic import BaseModel

import moon_calendar as mc

app = FastAPI(title="bouj moon calendar")


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
        m_b, l_b, d_b = mc.lunar_date(nm_local - timedelta(hours=1))
        m_a, l_a, d_a = mc.lunar_date(nm_local + timedelta(hours=1))
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
