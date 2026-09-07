"""
Fallback weather from MET Norway, for when Open-Meteo refuses us.

Open-Meteo's free tier is rate limited per IP, and a shared cloud egress IP is
exhausted by everyone else on it long before our own traffic matters - the
service returns "Daily API request limit exceeded" no matter how little we ask
for. Caching cannot fix a quota we do not control, so a second provider is the
only thing that keeps weather working.

MET Norway's Locationforecast is free, keyless and global. It gives current
conditions and a forecast but no history, so a farmer still gets today's
temperature, humidity and the days ahead - just not the month of rainfall
behind them. That is a real loss and the report says so rather than quietly
presenting a thinner answer as if it were the full one.

Their terms require a User-Agent identifying the application and a contact.
"""
from collections import defaultdict
from datetime import datetime

import requests

from config import WEATHER_TIMEOUT, METNO_USER_AGENT

URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
FORECAST_DAYS = 7


def fetch(lat, lon) -> str | None:
    """A weather report from MET Norway, or None if it too is unavailable."""
    try:
        response = requests.get(
            URL,
            params={"lat": round(float(lat), 4), "lon": round(float(lon), 4)},
            headers={"User-Agent": METNO_USER_AGENT},
            timeout=WEATHER_TIMEOUT,
        )
        response.raise_for_status()
        series = response.json()["properties"]["timeseries"]
    except Exception as e:
        print(f"Fallback weather failed for ({lat}, {lon}): {e}")
        return None

    if not series:
        return None

    lines = [f"Weather Report for ({lat}, {lon}):"]

    now = series[0]["data"]["instant"]["details"]
    parts = []
    if now.get("air_temperature") is not None:
        parts.append(f"temperature {now['air_temperature']}C")
    if now.get("relative_humidity") is not None:
        parts.append(f"humidity {now['relative_humidity']:.0f}%")
    if now.get("wind_speed") is not None:
        # MET reports m/s; farmers and the rest of this report use km/h.
        parts.append(f"wind {now['wind_speed'] * 3.6:.1f} km/h")
    if parts:
        lines.append("- Right now: " + ", ".join(parts))

    # Collapse the hourly series into daily highs, lows and rainfall.
    days = defaultdict(lambda: {"temps": [], "rain": 0.0})
    for point in series:
        try:
            day = datetime.fromisoformat(point["time"].replace("Z", "+00:00")).date()
        except Exception:
            continue
        details = point["data"]["instant"]["details"]
        if details.get("air_temperature") is not None:
            days[day]["temps"].append(details["air_temperature"])
        nxt = point["data"].get("next_6_hours") or point["data"].get("next_1_hours") or {}
        days[day]["rain"] += (nxt.get("details") or {}).get("precipitation_amount", 0.0)

    ordered = sorted(days.items())[:FORECAST_DAYS + 1]
    if ordered:
        today, today_data = ordered[0]
        if today_data["temps"]:
            lines.append(
                f"- Today's temp: {min(today_data['temps']):.1f}-"
                f"{max(today_data['temps']):.1f}C, "
                f"Rain: {today_data['rain']:.1f} mm"
            )

        forecast = []
        for day, data in ordered[1:]:
            if data["temps"]:
                forecast.append(
                    f"{day.isoformat()}: {min(data['temps']):.1f}-"
                    f"{max(data['temps']):.1f}C, {data['rain']:.1f} mm rain"
                )
        if forecast:
            lines.append(f"- {len(forecast)}-day Forecast:")
            lines.extend(forecast)

    # Say what is missing rather than let the model assume it has everything.
    lines.append("- Note: past rainfall history is not available from this source.")

    return "\n".join(lines) + "\n"
