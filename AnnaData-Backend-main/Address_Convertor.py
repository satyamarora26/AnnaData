"""
Place name to coordinates.

Two providers, tried in order:

  Google Maps  - used only when LOCATION_API_KEY is set. Better at messy input
                 and Indian village names, but needs a billing account.
  Nominatim    - OpenStreetMap's free geocoder, and the default. No key.

Nominatim's usage policy asks for at most one request per second, a User-Agent
that identifies the application, and that results are cached rather than
re-requested. All three are honoured here: calls are throttled, the agent
identifies itself, and results are memoised in-process on top of the permanent
per-farmer cache in the profile store - a given farmer is geocoded once, ever.

Returns (None, None) on any failure rather than raising, so a geocoding outage
degrades the answer instead of failing the request.
"""
import threading
import time

import requests

from config import (
    LOCATION_API_KEY,
    HTTP_TIMEOUT,
    NOMINATIM_URL,
    NOMINATIM_USER_AGENT,
    GEOCODE_COUNTRY,
)

# Nominatim asks for no more than one request per second.
_MIN_INTERVAL = 1.1
_last_call = 0.0
_throttle = threading.Lock()

# Districts repeat heavily across farmers, so an in-process cache saves most
# calls even before the per-farmer coordinates are stored.
_cache: dict[str, tuple] = {}
_CACHE_MAX = 512


def _throttled_get(url: str, params: dict, headers: dict):
    global _last_call
    with _throttle:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()
    return requests.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)


def _google(address: str):
    response = requests.get(
        "https://maps.googleapis.com/maps/api/geocode/json",
        params={"address": address, "key": LOCATION_API_KEY,
                "components": f"country:{GEOCODE_COUNTRY}"},
        timeout=HTTP_TIMEOUT,
    )
    response.raise_for_status()
    data = response.json()

    if data.get("status") == "OK" and data.get("results"):
        loc = data["results"][0]["geometry"]["location"]
        return loc["lat"], loc["lng"]

    print(f"Google geocoding returned {data.get('status')} for {address!r}")
    return None, None


def _nominatim(address: str):
    response = _throttled_get(
        NOMINATIM_URL,
        {
            "q": address,
            "format": "jsonv2",
            "limit": 1,
            # Farmer queries are Indian; the bias stops "Nagpur" resolving abroad.
            "countrycodes": GEOCODE_COUNTRY,
        },
        # Nominatim rejects requests without an identifying User-Agent.
        {"User-Agent": NOMINATIM_USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    results = response.json()

    if results:
        return float(results[0]["lat"]), float(results[0]["lon"])

    print(f"Nominatim found nothing for {address!r}")
    return None, None


def get_location(address: str):
    """Get (latitude, longitude) for a place, or (None, None)."""
    if not address:
        return None, None

    address = address.strip()
    if not address or address.lower() == "unknown":
        return None, None

    key = address.lower()
    if key in _cache:
        return _cache[key]

    provider = "google" if LOCATION_API_KEY else "nominatim"
    try:
        lat, lon = _google(address) if LOCATION_API_KEY else _nominatim(address)
    except Exception as e:
        print(f"Geocoding failed ({provider}) for {address!r}: {e}")
        return None, None

    if lat is not None and lon is not None:
        if len(_cache) >= _CACHE_MAX:
            _cache.clear()
        _cache[key] = (lat, lon)
        print(f"Geocoded {address!r} -> ({lat}, {lon}) via {provider}")

    return lat, lon
