"""
Weather from Open-Meteo (no API key required).

Days are now located by matching the actual date strings the API returns rather
than by fixed negative offsets, which silently reported the wrong day whenever
the response range differed from the request.
"""
import threading
import time

import requests
from datetime import date, datetime, timedelta, timezone

import weather_fallback
from config import (
    WEATHER_TIMEOUT,
    WEATHER_ATTEMPTS,
    WEATHER_CACHE_TTL,
    WEATHER_CACHE_PRECISION,
)

PAST_DAYS = 30
FORECAST_DAYS = 7

# Keyed by coarse coordinate, so every farmer in a district shares one reading.
_cache: dict[tuple, tuple] = {}
_cache_lock = threading.Lock()
_CACHE_MAX = 256

# Runtime state is observational only: /health reads it without probing either
# provider. The successful report is retained internally, while status() emits
# metadata only so provider text can never leak through readiness output.
_status_lock = threading.Lock()
_availability_state = "unknown"
_serving_provider: str | None = None
_last_error: str | None = None
_last_success_at: str | None = None
_last_success_provider: str | None = None
_last_successful_result: str | None = None


def _safe_error_code(value: str | None) -> str | None:
    if value is None:
        return None
    if value.startswith("open_meteo_") and value.replace("_", "").isalnum():
        return value
    if value == "weather_providers_unavailable":
        return value
    return "weather_provider_error"


def status() -> dict:
    """Return provider-state metadata without exposing a weather report."""
    with _status_lock:
        last_success = None
        if _last_successful_result is not None:
            last_success = {
                "provider": _last_success_provider,
                "at": _last_success_at,
            }
        return {
            "state": _availability_state,
            "provider": _serving_provider,
            "last_error": _safe_error_code(_last_error),
            "last_successful_result": last_success,
        }


def last_error() -> str | None:
    return status()["last_error"]


def is_available() -> bool:
    """Whether the last recorded weather-provider state is usable."""
    return status()["state"] in {"ready", "degraded"}


def _is_valid_report(report: object) -> bool:
    return (
        isinstance(report, str)
        and report.startswith("Weather Report for ")
        and ("\n- Right now:" in report or "\n- Today's temp:" in report)
    )


def _record_success(provider: str, report: str) -> None:
    global _availability_state, _serving_provider, _last_error
    global _last_success_at, _last_success_provider, _last_successful_result
    with _status_lock:
        _availability_state = "ready" if provider == "open-meteo" else "degraded"
        _serving_provider = provider
        _last_error = None
        _last_success_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        _last_success_provider = provider
        _last_successful_result = report


def _record_unavailable(error_code: str | None) -> None:
    global _availability_state, _serving_provider, _last_error
    with _status_lock:
        _availability_state = "unavailable"
        _serving_provider = None
        _last_error = _safe_error_code(error_code or "weather_providers_unavailable")


def _set_primary_error(error_code: str) -> None:
    global _last_error
    with _status_lock:
        _last_error = _safe_error_code(error_code)


def _cache_key(lat, lon) -> tuple:
    p = WEATHER_CACHE_PRECISION
    return (round(float(lat), p), round(float(lon), p))


def weather_openmeteo(lat, lon) -> str:
    key = _cache_key(lat, lon)
    now = time.monotonic()

    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < WEATHER_CACHE_TTL:
            provider = hit[2] if len(hit) > 2 else "open-meteo"
            _record_success(provider, hit[1])
            return hit[1]

    try:
        report = _fetch_weather(lat, lon)
    except Exception:
        _set_primary_error("open_meteo_invalid_response")
        report = "Weather data unavailable (unexpected response)."
    provider = "open-meteo"

    # Open-Meteo's limit is per IP and shared with everyone else on this host,
    # so being refused says nothing about our own usage and retrying will not
    # help. Fall back to a provider with its own quota.
    if not _is_valid_report(report):
        fallback = weather_fallback.fetch(lat, lon)
        if _is_valid_report(fallback):
            print(f"Weather served by fallback provider for ({lat}, {lon})")
            report = fallback
            provider = "met-norway"

    # Never cache a failure - the next farmer deserves a fresh attempt.
    if _is_valid_report(report):
        _record_success(provider, report)
        with _cache_lock:
            if len(_cache) >= _CACHE_MAX:
                _cache.clear()
            _cache[key] = (now, report, provider)
    else:
        _record_unavailable(last_error())

    return report


def _fetch_weather(lat, lon) -> str:
    today = date.today()
    start = today - timedelta(days=PAST_DAYS)
    end = today + timedelta(days=FORECAST_DAYS)

    params = {
        "latitude": lat,
        "longitude": lon,
        # Live conditions. Farmers ask "what is it like right now" directly,
        # and daily aggregates cannot answer that - humidity especially, which
        # has no daily equivalent and drives disease pressure advice.
        "current": "temperature_2m,relative_humidity_2m,precipitation,"
                   "wind_speed_10m,weather_code",
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,"
                 "weathercode,windspeed_10m_max",
        "start_date": start.isoformat(),
        "end_date": end.isoformat(),
        "timezone": "auto",
    }

    data = None
    for attempt in range(1, WEATHER_ATTEMPTS + 1):
        try:
            r = requests.get(
                "https://api.open-meteo.com/v1/forecast",
                params=params,
                timeout=WEATHER_TIMEOUT,
            )
            r.raise_for_status()
            data = r.json()
            break
        except Exception as e:
            # Retain a bounded diagnostic code only. Exception messages and
            # response bodies can contain request details and are never logged.
            status = getattr(getattr(e, "response", None), "status_code", "-")
            error_code = (
                f"open_meteo_http_{status}"
                if isinstance(status, int) or str(status).isdigit()
                else f"open_meteo_{type(e).__name__.lower()}"
            )
            _set_primary_error(error_code)
            print(f"Weather attempt {attempt}/{WEATHER_ATTEMPTS} failed "
                  f"for ({lat}, {lon}) ({_safe_error_code(error_code)})")
            if attempt < WEATHER_ATTEMPTS:
                time.sleep(attempt)

    if data is None:
        print(
            f"Weather lookup failed for ({lat}, {lon}) "
            f"after {WEATHER_ATTEMPTS} attempt(s)"
        )
        return "Weather data unavailable (lookup failed)."

    daily = data.get("daily")
    times = daily.get("time") if isinstance(daily, dict) else None
    required_series = (
        "temperature_2m_max",
        "temperature_2m_min",
        "precipitation_sum",
        "windspeed_10m_max",
    )
    if (
        not isinstance(times, list)
        or not times
        or any(not isinstance(value, str) or not value for value in times)
        or any(
            not isinstance(daily.get(key), list)
            or len(daily[key]) != len(times)
            or any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in daily[key]
            )
            for key in required_series
        )
    ):
        _set_primary_error("open_meteo_invalid_response")
        return "Weather data unavailable (unexpected response)."

    tmax = daily["temperature_2m_max"]
    tmin = daily["temperature_2m_min"]
    precip = daily["precipitation_sum"]
    wind = daily["windspeed_10m_max"]

    # Locate today by date string; fall back to the last past day available.
    today_str = today.isoformat()
    idx = times.index(today_str) if today_str in times else len(times) - FORECAST_DAYS - 1
    idx = max(0, min(idx, len(times) - 1))

    def num(values, i):
        return values[i]

    past_rain = [num(precip, i) for i in range(idx)]
    last_30_sum = sum(past_rain)
    last_10 = past_rain[-10:]
    last_10_avg = sum(last_10) / len(last_10) if last_10 else 0.0

    forecast_lines = []
    for i in range(idx + 1, len(times)):
        forecast_lines.append(
            f"{times[i]}: {num(tmin, i)}-{num(tmax, i)}C, "
            f"{num(precip, i)} mm rain, wind {num(wind, i)} km/h"
        )

    # Live conditions. Farmers ask what it is like right now, and a daily
    # aggregate cannot answer that - humidity especially, which has no daily
    # equivalent and is what drives fungal disease pressure.
    current = data.get("current") or {}
    now_parts = []
    for key, unit, label in (
        ("temperature_2m", "C", "temperature"),
        ("relative_humidity_2m", "%", "humidity"),
        ("precipitation", " mm", "precipitation"),
        ("wind_speed_10m", " km/h", "wind"),
    ):
        value = current.get(key)
        if value is not None:
            now_parts.append(f"{label} {value}{unit}")
    now_block = f"- Right now: {', '.join(now_parts)}\n" if now_parts else ""

    report = (
        f"Weather Report for ({lat}, {lon}):\n"
        f"{now_block}"
        f"- Today's temp: {num(tmin, idx)}-{num(tmax, idx)}C, "
        f"Rain: {num(precip, idx)} mm, Wind: {num(wind, idx)} km/h\n"
        f"- Last 10 days avg rainfall: {last_10_avg:.2f} mm/day\n"
        f"- Last 1 month total rainfall: {last_30_sum:.2f} mm\n"
        f"- {len(forecast_lines)}-day Forecast:\n" + "\n".join(forecast_lines)
    )
    if not _is_valid_report(report):
        _set_primary_error("open_meteo_invalid_response")
        return "Weather data unavailable (unexpected response)."
    return report
