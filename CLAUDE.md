# CLAUDE.md — bouj-backend

## Project overview

`moon_calendar.py` is a CLI lunar calendar that computes Chinese lunisolar dates from exact astronomical timestamps using the **skyfield** library. It is the core logic that will be wrapped in a FastAPI service.

The next major milestone is the **FastAPI wrapper** — exposing `lunar_date()` and gate-day detection as HTTP endpoints.

---

## What makes this calendar unique

### 1. Exact timestamps, not calendar-date approximations

Every astronomical boundary (new moon, solar marker) is compared at the level of the precise UTC moment, never rounded to a calendar day. A solar marker belongs to a month if its exact UTC timestamp falls in `[nm_start, nm_end)` — full stop. No timezone-specific date comparison.

This means results will differ from the traditional Chinese calendar (which uses Beijing time / CST for date-rounding). That divergence is **intentional and correct** for this project. Do not "fix" it toward the traditional calendar.

### 2. Location-based calendars

Different locations produce different calendars because the user's local timezone determines which calendar day a new moon or lunar day falls on. Month numbering (zhongqi membership for leap detection) uses UTC timestamps consistently, but display — day numbers, gate-day dates — is always in the user's local time.

### 3. The gate day

The day of the new moon is **liminal**: it is simultaneously the last day of the closing month and the first day of the opening month. The app always shows both readings on a gate day (`gate day — sixth moon day thirty / seventh moon day one`), regardless of whether the user is before or after the exact new moon time. This is the most distinctive UX concept in the project.

The gate day matters for leap month detection too: because we use the exact new moon timestamp as the month boundary (not the calendar day), a solar marker that falls on the same local calendar day as the new moon but before the exact new moon moment belongs to the *previous* month. This is more astronomically precise than any published Chinese calendar.

### 4. Leap months via exact solar longitude

A month is a leap month if no zhōngqì (major solar marker, sun at a multiple of 30° ecliptic longitude) occurs within `[nm_start, nm_end)`. The 12 zhōngqì are at 0°, 30°, 60° … 330°. The first month in a 13-month suì that lacks a zhōngqì takes the same number as the preceding month and is flagged `is_leap=True`.

---

## Architecture

### Key functions

| Function | Purpose |
|---|---|
| `lunar_date(local_dt)` | Main entry point — returns `(month_num, is_leap, day_num)` |
| `_gate_new_moon(date_obj, tz)` | Returns the new moon `Time` if one falls on that local date, else `None` |
| `_locate_sui(prev_new, year)` | Finds and caches the suì (Chinese year) containing a given new moon |
| `_build_sui(m11_start, m11_next)` | Builds the list of months in a suì via chained `_next_new_moon` calls |
| `_assign_months(sui)` | Assigns month numbers and `is_leap` flags |
| `_month_zhongqi(nm_start, nm_end)` | Returns the zhōngqì in a month interval, or `None` |
| `print_sui_audit(now)` | CLI `--audit` flag — prints full suì table for verification |

### Caching

- `_SF` — skyfield loader/ephemeris (loaded once per process)
- `_m11_cache` — month-11-start per Gregorian anchor year
- `_sui_cache` — full `(sui, assignments)` per anchor year; avoids recomputing 13+ solar longitude calls per `lunar_date()` call. Critical for the FastAPI server.

### Known design decisions encoded in the code

- **1-day tolerance in `_build_sui`**: `while cursor.tt + 1.0 < m11_next.tt` — guards against floating-point divergence where `_previous_new_moon(WS)` and a chained `_next_new_moon` converge to the same new moon at microscopically different TT values.
- **0.01 TT day tolerance in `lunar_date`**: matches `prev_new` (computed backward from current time) to the `nm_start` in the suì (computed forward from month-11). They're the same astronomical event via different code paths.
- **Gate day window is midnight-to-midnight** (not `23:59:59`) so no new moon at the last second of a day is missed.
- **`_assign_months` asserts `len(sui) in (12, 13)`** — the 1-day tolerance fix makes a spurious 14th month impossible, but the assertion is the canary.

---

## Dependencies

```
skyfield          # astronomy (de421.bsp ephemeris, cached at ~/.cache/skyfield)
timezonefinder    # lat/lon → IANA timezone name
pytz              # timezone objects for localize()
```

FastAPI deps will be added when the wrapper is built.

---

## CLI flags

```
moon              today's lunar date
moon -d           lunar phase details (next/recent phases)
moon -o           look up another date
moon -l           location menu
moon -r           reset saved locations
moon --audit      full suì month table with zhōngqì mapping
```

Config is stored at `~/.config/moon-calendar/config.json`.

---

## What NOT to do

- Do not add Beijing-time (CST/UTC+8) rounding to zhōngqì membership — it breaks location independence and is contrary to the core design.
- Do not use `if skyfield_time:` — skyfield `Time` objects raise `TypeError` on truthiness tests. Always check `if t is not None:`.
- Do not commit the ephemeris file (`de421.bsp`) — it is ~17 MB and cached at `~/.cache/skyfield`, not in the repo.
