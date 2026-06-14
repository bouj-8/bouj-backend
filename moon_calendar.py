#!/usr/bin/env python3
"""Moon calendar CLI - displays the current lunar date in natural language."""

import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "moon-calendar" / "config.json"
SKYFIELD_CACHE = Path.home() / ".cache" / "skyfield"


def bold(text: str) -> str:
    return f"\033[1m{text}\033[0m"

MONTH_ORDINALS = [
    "", "first", "second", "third", "fourth", "fifth", "sixth",
    "seventh", "eighth", "ninth", "tenth", "eleventh", "twelfth", "thirteenth",
]

DAY_ONES = [
    "", "one", "two", "three", "four", "five", "six", "seven",
    "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
    "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
]
DAY_TENS = ["", "", "twenty", "thirty"]


def day_to_words(n: int) -> str:
    if 1 <= n <= 19:
        return DAY_ONES[n]
    tens, ones = divmod(n, 10)
    return DAY_TENS[tens] if ones == 0 else f"{DAY_TENS[tens]}-{DAY_ONES[ones]}"


def month_to_words(n: int) -> str:
    if 1 <= n < len(MONTH_ORDINALS):
        return MONTH_ORDINALS[n]
    return f"{n}th"


def load_config() -> dict | None:
    if not CONFIG_PATH.exists():
        return None
    data = json.loads(CONFIG_PATH.read_text())
    if "name" in data:
        return {"locations": [{"name": data["name"], "lat": data["lat"], "lon": data["lon"]}]}
    return data


def save_config(config: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(config, indent=2))


def prompt_location() -> dict:
    name = input("Location name (e.g. San Francisco): ").strip()
    while True:
        try:
            lat = float(input("Latitude  (e.g.  37.7749): ").strip())
            lon = float(input("Longitude (e.g. -122.4194): ").strip())
            if not (-90 <= lat <= 90 and -180 <= lon <= 180):
                raise ValueError
            break
        except ValueError:
            print("Please enter valid coordinates.")
    return {"name": name, "lat": lat, "lon": lon}


def print_menu(locations: list) -> None:
    print()
    for i, loc in enumerate(locations[:9], 1):
        print(f"  {i}  {loc['name']}")
    print(f"  0  add new location")
    print(f"  x  delete a location")
    print()


def select_location(config: dict, keep_default: bool = False) -> dict:
    locations = config["locations"]

    while True:
        print_menu(locations)

        try:
            raw = input("location: ").strip().lower()
            if not raw:
                continue
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)

        if raw == "x":
            if len(locations) == 1:
                print("  can't delete the only saved location")
                continue
            try:
                choice = int(input("  delete: ").strip())
            except (ValueError, EOFError, KeyboardInterrupt):
                continue
            if 1 <= choice <= min(9, len(locations)):
                locations.pop(choice - 1)
            continue

        try:
            choice = int(raw)
        except ValueError:
            continue

        if choice == 0:
            print()
            loc = prompt_location()
            locations.insert(1 if keep_default else 0, loc)
            config["locations"] = locations[:9]
            return loc
        elif 1 <= choice <= min(9, len(locations)):
            loc = locations.pop(choice - 1)
            if keep_default and choice != 1:
                locations.insert(1, loc)
            else:
                locations.insert(0, loc)
            return loc


def _location_tz(lat: float, lon: float):
    try:
        from timezonefinder import TimezoneFinder
        import pytz
        tz_name = TimezoneFinder().timezone_at(lat=lat, lng=lon)
        return pytz.timezone(tz_name) if tz_name else timezone.utc
    except ImportError:
        return timezone.utc


def local_now(lat: float, lon: float) -> datetime:
    return datetime.now(_location_tz(lat, lon))


def _localize_noon(date_obj, tz) -> datetime:
    naive = datetime(date_obj.year, date_obj.month, date_obj.day, 12, 0, 0)
    try:
        return tz.localize(naive)
    except AttributeError:
        return naive.replace(tzinfo=tz)


_DATE_FORMATS = [
    "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y",
    "%d %b %Y", "%d %B %Y",
    "%b %d, %Y", "%B %d, %Y",
    "%b %d %Y",  "%B %d %Y",
]


# ── skyfield core ───────────────────────────────────────────────────────────

_SF: dict = {}

PHASE_NAMES = {0: "new moon", 1: "first quarter", 2: "full moon", 3: "last quarter"}

# Major solar markers (中气 zhōngqì): sun at each multiple of 30° ecliptic longitude.
# Index = sector number (0–11), where sector n covers [n*30°, (n+1)*30°).
# A lunar month containing no zhongqi is a leap month.
ZHONGQI = [
    (  0, "Vernal Equinox"),    # 春分  Chūnfēn
    ( 30, "Grain Rain"),        # 谷雨  Gǔyǔ
    ( 60, "Grain Buds"),        # 小满  Xiǎomǎn
    ( 90, "Summer Solstice"),   # 夏至  Xiàzhì
    (120, "Major Heat"),        # 大暑  Dàshǔ
    (150, "End of Heat"),       # 处暑  Chǔshǔ
    (180, "Autumnal Equinox"),  # 秋分  Qiūfēn
    (210, "Frost's Descent"),   # 霜降  Shuāngjiàng
    (240, "Minor Snow"),        # 小雪  Xiǎoxuě
    (270, "Winter Solstice"),   # 冬至  Dōngzhì
    (300, "Major Cold"),        # 大寒  Dàhán
    (330, "Rain Water"),        # 雨水  Yǔshuǐ
]


def _get_sf():
    if not _SF:
        from skyfield.api import Loader
        from skyfield import almanac
        loader = Loader(str(SKYFIELD_CACHE))
        _SF['ts'] = loader.timescale()
        _SF['eph'] = loader('de421.bsp')
        _SF['almanac'] = almanac
    return _SF['ts'], _SF['eph'], _SF['almanac']


def _find_phases(t0_tt: float, t1_tt: float) -> list:
    """Return [(Time, phase_name)] for all phase events in the TT JD range."""
    ts, eph, al = _get_sf()
    t0 = ts.tt_jd(t0_tt)
    t1 = ts.tt_jd(t1_tt)
    times, phases = al.find_discrete(t0, t1, al.moon_phases(eph))
    return [(times[i], PHASE_NAMES[int(phases[i])]) for i in range(len(times))]


def _previous_new_moon(t):
    for lookback in (40, 80):
        candidates = [time for time, name in _find_phases(t.tt - lookback, t.tt) if name == "new moon"]
        if candidates:
            return candidates[-1]
    raise RuntimeError("Could not find previous new moon")


def _next_new_moon(t):
    candidates = [time for time, name in _find_phases(t.tt, t.tt + 40) if name == "new moon"]
    if not candidates:
        raise RuntimeError("No new moon found in 40-day window")
    return candidates[0]


def _next_full_moon(t):
    candidates = [time for time, name in _find_phases(t.tt, t.tt + 40) if name == "full moon"]
    if not candidates:
        raise RuntimeError("No full moon found in 40-day window")
    return candidates[0]


def _next_winter_solstice(t):
    ts, eph, al = _get_sf()
    t1 = ts.tt_jd(t.tt + 370)
    times, events = al.find_discrete(t, t1, al.seasons(eph))
    for i in range(len(times)):
        if int(events[i]) == 3:
            return times[i]
    raise RuntimeError("Could not find winter solstice")


def _time_from_dt(dt: datetime):
    ts, _, _ = _get_sf()
    return ts.from_datetime(dt)


def _dt_from_time(t, tz) -> datetime:
    return t.utc_datetime().replace(tzinfo=timezone.utc).astimezone(tz)


def _solar_longitude(t) -> float:
    """Return sun's apparent ecliptic longitude in degrees [0, 360) at time t."""
    _, eph, _ = _get_sf()
    astrometric = eph['earth'].at(t).observe(eph['sun']).apparent()
    _, lon, _ = astrometric.ecliptic_latlon(epoch='date')
    return lon.degrees % 360


def _month_zhongqi(nm_start, nm_end):
    """Return (longitude, name) of the zhongqi in [nm_start, nm_end), or None."""
    lon_start = _solar_longitude(nm_start)
    lon_end   = _solar_longitude(nm_end)
    if lon_end < lon_start:   # sun crossed 0°/360° boundary
        lon_end += 360
    if int(lon_end / 30) > int(lon_start / 30):
        sector = (int(lon_start / 30) + 1) % 12
        return ZHONGQI[sector]
    return None


def _gate_new_moon(date_obj, tz):
    """Return the new moon Time if one occurs on date_obj in tz, else None."""
    naive_start = datetime(date_obj.year, date_obj.month, date_obj.day)
    naive_end   = naive_start + timedelta(days=1)
    try:
        dt_start = tz.localize(naive_start)
        dt_end   = tz.localize(naive_end)
    except AttributeError:
        dt_start = naive_start.replace(tzinfo=tz)
        dt_end   = naive_end.replace(tzinfo=tz)
    candidates = [t for t, name in _find_phases(
        _time_from_dt(dt_start).tt, _time_from_dt(dt_end).tt) if name == "new moon"]
    return candidates[0] if candidates else None


# ── Chinese lunisolar calendar ──────────────────────────────────────────────

_m11_cache: dict = {}
_sui_cache: dict = {}


def _month_11_start_for(gregorian_year: int):
    """Return the new moon just before the winter solstice of gregorian_year."""
    if gregorian_year not in _m11_cache:
        ts, _, _ = _get_sf()
        t0 = ts.utc(gregorian_year, 11, 1)
        solstice = _next_winter_solstice(t0)
        _m11_cache[gregorian_year] = _previous_new_moon(solstice)
    return _m11_cache[gregorian_year]


def _build_sui(m11_start, m11_next) -> list:
    """Return [(nm_start, nm_end, zhongqi_or_None)] for every month in the suì.

    Uses a 1-day tolerance in the loop guard because m11_next is computed via
    _previous_new_moon(WS) while the chained _next_new_moon calls may land on
    the same astronomical new moon at a microscopically different TT value.
    New moons are ~29.5 days apart so 1 day is a safe tolerance.
    """
    months = []
    cursor = m11_start
    while cursor.tt + 1.0 < m11_next.tt:
        nm_end = _next_new_moon(cursor)
        months.append((cursor, nm_end, _month_zhongqi(cursor, nm_end)))
        cursor = nm_end
    return months


def _assign_months(sui: list) -> list:
    """Return [(nm_start, nm_end, month_num, is_leap)] with proper Chinese month numbers.

    Month 11 always starts the suì (it contains the winter solstice).
    In a 13-month suì the first month lacking a zhongqi is the leap month;
    it takes the same number as the preceding month and is flagged is_leap=True.
    """
    if len(sui) not in (12, 13):
        raise RuntimeError(f"Unexpected suì length {len(sui)} — expected 12 or 13")

    is_leap_sui = len(sui) == 13
    assignments = []
    current_num = 11
    leap_found  = False

    for i, (nm_start, nm_end, zhongqi) in enumerate(sui):
        if i == 0:
            assignments.append((nm_start, nm_end, 11, False))
            current_num = 12
        elif is_leap_sui and zhongqi is None and not leap_found:
            prev_num = assignments[-1][2]
            assignments.append((nm_start, nm_end, prev_num, True))
            leap_found = True
        else:
            assignments.append((nm_start, nm_end, current_num, False))
            current_num = current_num % 12 + 1

    return assignments


def _locate_sui(prev_new, gregorian_year: int):
    """Return (sui, assignments) for the suì that contains prev_new."""
    for y in [gregorian_year - 1, gregorian_year]:
        m11      = _month_11_start_for(y)
        m11_next = _month_11_start_for(y + 1)
        if m11.tt <= prev_new.tt < m11_next.tt:
            if y not in _sui_cache:
                sui = _build_sui(m11, m11_next)
                _sui_cache[y] = (sui, _assign_months(sui))
            return _sui_cache[y]
    raise RuntimeError("Could not locate suì for given new moon")


def lunar_date(local_dt: datetime) -> tuple[int, bool, int]:
    """Return (month_num, is_leap, day_num) in the Chinese lunisolar calendar."""
    utc_dt   = local_dt.astimezone(timezone.utc)
    t        = _time_from_dt(utc_dt)
    prev_new = _previous_new_moon(t)

    prev_new_local = _dt_from_time(prev_new, local_dt.tzinfo)
    lunar_day = (local_dt.date() - prev_new_local.date()).days + 1

    _, assignments = _locate_sui(prev_new, utc_dt.year)

    for nm_start, _, month_num, is_leap in assignments:
        if abs(nm_start.tt - prev_new.tt) < 0.01:
            return month_num, is_leap, min(30, max(1, lunar_day))

    raise RuntimeError("Could not match new moon to a month in the suì")


# ── audit (--audit) ─────────────────────────────────────────────────────────

def print_sui_audit(now: datetime) -> None:
    """Print the full suì month table so the zhongqi mapping can be verified."""
    utc_dt   = now.astimezone(timezone.utc)
    t        = _time_from_dt(utc_dt)
    prev_new = _previous_new_moon(t)

    sui, assignments = _locate_sui(prev_new, utc_dt.year)

    start_date = _dt_from_time(assignments[0][0],  timezone.utc).strftime('%Y-%m-%d')
    end_date   = _dt_from_time(assignments[-1][1], timezone.utc).strftime('%Y-%m-%d')

    print(f"\nsuì  {start_date} → {end_date}  ({len(sui)} months)\n")

    for (nm_start, nm_end, month_num, is_leap), (_, _, zhongqi) in zip(assignments, sui):
        label   = ("leap " if is_leap else "") + month_to_words(month_num) + " moon"
        current = "◀" if abs(nm_start.tt - prev_new.tt) < 0.01 else " "
        s_date  = _dt_from_time(nm_start, timezone.utc).strftime('%b %-d')
        e_date  = _dt_from_time(nm_end,   timezone.utc).strftime('%b %-d')

        if zhongqi:
            lon, name = zhongqi
            print(f"{current} {label:<24s}  {s_date}–{e_date}  {lon:3.0f}°  {name}")
        else:
            print(f"{current} {label:<24s}  {s_date}–{e_date}   —   no zhongqi  ← leap")

    print()


# ── details (-d) helpers ────────────────────────────────────────────────────

def _major_phases_near(t) -> list:
    """Return sorted [(Time, name)] for all major phases in a 120-day window around t."""
    return sorted(_find_phases(t.tt - 60, t.tt + 60), key=lambda x: x[0].tt)


def _fmt_time(dt: datetime) -> str:
    return dt.strftime("%-I:%M%p").lower()


def _day_label_past(event_dt: datetime, now_dt: datetime) -> str:
    delta = (event_dt.date() - now_dt.date()).days
    return "today" if delta == 0 else "yesterday"


def _day_label_future(event_dt: datetime, now_dt: datetime) -> str:
    delta = (event_dt.date() - now_dt.date()).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta <= 7:
        return f"this {event_dt.strftime('%A')}"
    if delta <= 14:
        return f"next {event_dt.strftime('%A')}"
    return event_dt.strftime("on %B %-d")


def _past_sentence(name: str, dt: datetime, now_dt: datetime) -> str:
    day, t = _day_label_past(dt, now_dt), _fmt_time(dt)
    if name == "new moon":
        return f"The new moon was {day} at {t}."
    if name == "full moon":
        return f"The full moon was {day} at {t}."
    if name in ("first quarter", "last quarter"):
        return f"The {name} moon was {day} at {t}."
    return f"The moon reached {name} {day} at {t}."


def _next_sentence(name: str, dt: datetime, now_dt: datetime) -> str:
    day, t = _day_label_future(dt, now_dt), _fmt_time(dt)
    if name == "new moon":
        return f"The next phase is the new moon, {day} at {t}."
    if name == "full moon":
        return f"The next phase is the full moon, {day} at {t}."
    if name in ("first quarter", "last quarter"):
        return f"The next phase is the {name} moon, {day} at {t}."
    return f"The next phase is a {name} moon, {day} at {t}."


def print_details(now: datetime) -> None:
    tz      = now.tzinfo
    utc_now = now.astimezone(timezone.utc)
    t       = _time_from_dt(utc_now)
    window_start_tt = t.tt - 36 / 24.0

    all_phases = _major_phases_near(t)

    recent = next(
        ((time, name) for time, name in reversed(all_phases)
         if window_start_tt <= time.tt <= t.tt),
        None,
    )
    upcoming = next(
        ((time, name) for time, name in all_phases if time.tt > t.tt),
        None,
    )

    next_full_t = _next_full_moon(t)
    next_new_t  = _next_new_moon(t)
    if next_full_t.tt < next_new_t.tt:
        fn_time, fn_name = next_full_t, "full moon"
    else:
        fn_time, fn_name = next_new_t, "new moon"

    print()

    if recent:
        time, name = recent
        print(_past_sentence(name, _dt_from_time(time, tz), now))

    if upcoming:
        time, name = upcoming
        print(_next_sentence(name, _dt_from_time(time, tz), now))

    upcoming_is_fn = upcoming and abs(upcoming[0].tt - fn_time.tt) < 0.01
    if not upcoming_is_fn:
        fn_local   = _dt_from_time(fn_time, tz)
        delta_days = (fn_local.date() - now.date()).days
        day_word   = "day" if delta_days == 1 else "days"
        weekday    = fn_local.strftime("%A")
        print(f"The upcoming {fn_name} is {delta_days} {day_word} from now, on {weekday}.")


# ── UI ───────────────────────────────────────────────────────────────────────

def _format_gate_day(nm_local: datetime, tz) -> str:
    m_b, l_b, d_b = lunar_date(nm_local - timedelta(hours=1))
    m_a, l_a, d_a = lunar_date(nm_local + timedelta(hours=1))
    closing = ("leap " if l_b else "") + f"{month_to_words(m_b)} moon day {day_to_words(d_b)}"
    opening = ("leap " if l_a else "") + f"{month_to_words(m_a)} moon day {day_to_words(d_a)}"
    return f"gate day — {closing} / {opening}"


def other_date_menu(config: dict) -> None:
    loc = select_location(config, keep_default=True)
    save_config(config)

    tz = _location_tz(loc["lat"], loc["lon"])

    print()
    while True:
        try:
            raw = input("  Date (e.g. 2024-01-15): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return
        if not raw:
            continue

        padded = raw
        if len(raw) > 1 and raw[0].isdigit():
            parts = raw.split("-", 1)
            if len(parts) == 2 and parts[0].isdigit() and len(parts[0]) < 4:
                padded = parts[0].zfill(4) + "-" + parts[1]

        date_obj = None
        for fmt in _DATE_FORMATS:
            for candidate in ({raw, padded} if padded != raw else {raw}):
                try:
                    date_obj = datetime.strptime(candidate, fmt).date()
                    break
                except ValueError:
                    continue
            if date_obj:
                break

        if date_obj is None:
            print("  Couldn't read that — try YYYY-MM-DD.")
            continue
        break

    print()
    nm = _gate_new_moon(date_obj, tz)
    if nm is not None:
        nm_local = _dt_from_time(nm, tz)
        print(bold(_format_gate_day(nm_local, tz)))
        print()
        print(f"@ {loc['name']}, {date_obj.strftime('%B %-d, %Y')}")
        print(f"new moon at {_fmt_time(nm_local)}")
    else:
        dt_local = _localize_noon(date_obj, tz)
        month_n, is_leap, day_n = lunar_date(dt_local)
        print(bold(f"{'leap ' if is_leap else ''}{month_to_words(month_n)} moon day {day_to_words(day_n)}"))
        print()
        print(f"@ {loc['name']}, {date_obj.strftime('%B %-d, %Y')}")


# ── main ────────────────────────────────────────────────────────────────────

def main() -> None:
    if "--help" in sys.argv or "-h" in sys.argv:
        print("\n  moon          show today's lunar date")
        print("  moon -d       lunar phase details")
        print("  moon -o       look up another date")
        print("  moon -l       location menu")
        print("  moon -r       reset saved locations")
        print("  moon --audit  show the full suì month table")
        print("  man moon      full documentation\n")
        return

    if "--reset" in sys.argv or "-r" in sys.argv:
        CONFIG_PATH.unlink(missing_ok=True)
        print("Location cleared. Run again to set a new location.")
        return

    config = load_config()
    if config is None:
        print("Welcome to Moon Calendar! Enter your location to get started.\n")
        config = {"locations": [prompt_location()]}
        save_config(config)
    elif "--location" in sys.argv or "-l" in sys.argv:
        select_location(config)
        save_config(config)

    if "--other" in sys.argv or "-o" in sys.argv:
        other_date_menu(config)
        return

    loc = config["locations"][0]
    now = local_now(loc["lat"], loc["lon"])

    if "--audit" in sys.argv:
        print_sui_audit(now)
        return

    print()
    nm = _gate_new_moon(now.date(), now.tzinfo)
    if nm is not None:
        nm_local = _dt_from_time(nm, now.tzinfo)
        print(bold(_format_gate_day(nm_local, now.tzinfo)))
        print()
        print(f"@ {loc['name']}")
        print(f"new moon at {_fmt_time(nm_local)}")
    else:
        month_n, is_leap, day_n = lunar_date(now)
        print(bold(f"{'leap ' if is_leap else ''}{month_to_words(month_n)} moon day {day_to_words(day_n)}"))
        print()
        print(f"@ {loc['name']}")

    if "--details" in sys.argv or "-d" in sys.argv:
        print_details(now)


if __name__ == "__main__":
    main()
