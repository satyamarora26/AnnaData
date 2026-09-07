"""
Soil properties from OpenLandMap via Google Earth Engine.

Values are read at b0 - the surface layer - which is the depth agronomic advice
is concerned with. Both source datasets store scaled integers, and getting the
scaling wrong is not a cosmetic problem: organic carbon reported at twice its
real value turns "low, add compost" into "adequate".

  pH             stored x10   -> divide by 10        (4.2-11.0)
  organic carbon stored /5    -> multiply by 5 = g/kg, and /10 again for %

Earth Engine is optional; if it is not configured the tool returns a short
notice the prompt can safely include.
"""
from concurrent.futures import ThreadPoolExecutor, as_completed

import startup

SAMPLE_BUFFER_M = 500

# USDA texture classes as OpenLandMap actually codes them. This table was
# inverted before - it read 1 as Sand and 12 as Clay, when the dataset is the
# other way round - so every reading came back as close to the opposite of the
# real soil. Nagpur's black cotton clay was being reported to farmers as sand,
# which reverses the irrigation and drainage advice that follows from it.
TEXTURE_MAP = {
    1: "Clay", 2: "Silty clay", 3: "Sandy clay", 4: "Clay loam",
    5: "Silty clay loam", 6: "Sandy clay loam", 7: "Loam", 8: "Silty loam",
    9: "Sandy loam", 10: "Silt", 11: "Loamy sand", 12: "Sand",
}

# Indian Soil Health Card interpretation bands. The rating drives the advice
# far more than the raw number does.
def _rate_organic_carbon(percent: float) -> str:
    if percent < 0.5:
        return "low"
    if percent <= 0.75:
        return "medium"
    return "high"


def _rate_ph(ph: float) -> str:
    if ph < 5.5:
        return "strongly acidic"
    if ph < 6.5:
        return "slightly acidic"
    if ph <= 7.5:
        return "neutral"
    if ph <= 8.5:
        return "slightly alkaline"
    return "strongly alkaline"


def _sample(image_id: str, point, scale: int = 250, categorical: bool = False):
    """Read band b0 near a point, or None where there is no data.

    reduceRegion over a small buffer rather than sampling a single pixel: a
    lone pixel on a coast or water body returns null and raises, and one pixel
    of a 250 m global model is noisy. Texture is a USDA class, so it takes the
    most common value in the neighbourhood - averaging class numbers would
    invent a soil type that is not there.
    """
    import ee

    reducer = ee.Reducer.mode() if categorical else ee.Reducer.mean()
    value = (
        ee.Image(image_id)
        .select("b0")
        .reduceRegion(reducer, point.buffer(SAMPLE_BUFFER_M), scale)
        .get("b0")
        .getInfo()
    )
    return None if value is None else float(value)


def soil_tool(lat: float, lon: float) -> str:
    """Return a soil report for a coordinate, or an unavailable notice."""
    if not startup.init_earth_engine():
        return "Soil data unavailable (Earth Engine not configured)."

    try:
        import ee

        point = ee.Geometry.Point(lon, lat)

        # Each property is a separate round trip to Earth Engine, so running
        # them in sequence cost roughly three times what it needed to. They are
        # independent, and one missing layer must not cost the other two.
        jobs = {
            "texture": ("OpenLandMap/SOL/SOL_TEXTURE-CLASS_USDA-TT_M/v02", True),
            "ph":      ("OpenLandMap/SOL/SOL_PH-H2O_USDA-4C1A2A_M/v02", False),
            "soc":     ("OpenLandMap/SOL/SOL_ORGANIC-CARBON_USDA-6A1C_M/v02", False),
        }

        readings = {}
        with ThreadPoolExecutor(max_workers=len(jobs)) as pool:
            futures = {
                pool.submit(_sample, image_id, point, 250, categorical): name
                for name, (image_id, categorical) in jobs.items()
            }
            for future in as_completed(futures):
                name = futures[future]
                try:
                    readings[name] = future.result()
                except Exception as e:
                    print(f"Soil {name} unavailable at ({lat}, {lon}): {e}")

        lines = [f"Soil Report for ({lat}, {lon}):"]

        if readings.get("texture") is not None:
            raw = readings["texture"]
            lines.append(
                f"- Soil texture (USDA class): "
                f"{TEXTURE_MAP.get(int(round(raw)), f'Unknown ({raw})')}"
            )

        if readings.get("ph") is not None:
            ph = readings["ph"] / 10.0
            lines.append(f"- Soil pH (H2O): {ph:.1f} ({_rate_ph(ph)})")

        if readings.get("soc") is not None:
            g_per_kg = readings["soc"] * 5.0
            percent = g_per_kg / 10.0
            lines.append(
                f"- Soil organic carbon: {percent:.2f}% "
                f"({g_per_kg:.1f} g/kg, {_rate_organic_carbon(percent)})"
            )

        if len(lines) == 1:
            return "Soil data unavailable (no readings at this location)."

        return "\n".join(lines) + "\n"

    except Exception as e:
        print(f"Soil lookup failed for ({lat}, {lon}): {e}")
        return "Soil data unavailable (lookup failed)."
