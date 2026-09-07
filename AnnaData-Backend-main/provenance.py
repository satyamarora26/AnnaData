"""
What AnnaData can do, and where its answers come from.

Two different questions get asked, and they need different answers.

"What can you help me with?" wants capabilities, in the farmer's terms - pest
control, fertiliser, when to sow - not the names of the APIs behind them. No
farmer cares that the soil figure came from OpenLandMap until they ask where it
came from.

"How do you know about my soil?" wants provenance, and deserves the truth
rather than a recalled account of how soil is tested in general.

Both are assembled from live state: which integrations are configured, how many
registered pesticide uses are loaded, which scheme documents are in the store.
An earlier version of this file listed three data sources and was never updated
as the dose table and the document store were added, so a farmer asking what
the service could do was told a smaller and older truth than the real one - and
asked about government schemes, was told there were none while the store held
eighty-six passages about them. A self-description that is not derived from the
system is a self-description that goes stale silently.
"""
import config
import db
import knowledge
import Mandi_Price_Tool
import msp
import startup
import Web_Crawler


def _scheme_names() -> list[str]:
    """Which schemes the document store actually covers."""
    if not db.is_available():
        return []
    try:
        with db.connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT title, source FROM documents"
            ).fetchall()
    except Exception:
        return []

    names = set()
    for title, source in rows:
        text = f"{title or ''} {source or ''}"
        for key, label in (
            ("kisan samman", "PM-KISAN"), ("pm-kisan", "PM-KISAN"),
            ("fasal bima", "Pradhan Mantri Fasal Bima Yojana (crop insurance)"),
            ("credit card", "Kisan Credit Card"),
            ("soil health", "Soil Health Card"),
        ):
            if key in text.lower():
                names.add(label)
    return sorted(names)


def capabilities() -> list[str]:
    """What a farmer can actually ask for, in their terms."""
    items = []

    counts = knowledge.counts()
    if counts.get("pesticide_uses"):
        items.append(
            f"Pests and diseases - what is wrong with a crop, and which "
            f"pesticide is officially approved for it, at what dose and how "
            f"long to wait before harvest. Covers {counts['pesticide_uses']:,} "
            f"registered uses from the Government of India's pesticide register."
        )

    if startup.is_available():
        items.append(
            "Soil - texture, pH and organic carbon for the farmer's own "
            "location, and what they mean for fertiliser and drainage."
        )

    items.append(
        "Weather - what it is doing now, the rain of the past month, and the "
        "week ahead, with what that means for spraying, irrigation and drainage."
    )

    items.append(
        "Sowing and the season - whether it is the right time to sow a crop, "
        "and when the next window opens."
    )

    n_msp = msp.counts().get("msp_commodities") or 0
    if n_msp:
        items.append(
            f"Support prices - the government's guaranteed floor price for "
            f"{n_msp} major crops, which holds all marketing year."
        )
    # Announcing a capability that is currently failing misleads a farmer just
    # as surely as denying one that works. The government price service has been
    # timing out for days, so it is not offered while that is true.
    if Mandi_Price_Tool.is_available():
        items.append(
            "Market prices - daily mandi rates for a crop in the farmer's state."
        )

    schemes = _scheme_names()
    if schemes:
        items.append(
            "Government schemes - eligibility, benefits and how to apply. "
            "Currently covers " + ", ".join(schemes) + "."
        )
    elif Web_Crawler.is_available():
        items.append("Government schemes and post-harvest storage.")

    items.append(
        "Fertiliser and nutrition - how much to apply and when, taking the "
        "soil and the crop's stage into account."
    )

    if db.is_available():
        items.append(
            "It remembers the farmer's district and crops so they need not "
            "repeat themselves, and they can erase everything by replying STOP."
        )

    items.append(
        "On the website a farmer can also send a photo of a diseased crop or a "
        "voice message instead of typing."
    )

    return items


def data_sources() -> list[str]:
    """Where the answers come from, for when that is what was asked."""
    sources = []

    if knowledge.counts().get("pesticide_uses"):
        sources.append(
            "Pesticide doses: the CIB&RC Major Uses of Pesticides registers "
            "published by the Directorate of Plant Protection, Government of "
            "India - the statutory list of what may legally be used on which "
            "crop, for which pest, at what dose."
        )

    if startup.is_available():
        sources.append(
            "Soil: satellite soil maps from OpenLandMap, read at about 250 "
            "metre resolution. A model estimate for the area, not a laboratory "
            "test of the farmer's field."
        )

    sources.append(
        "Weather: the Open-Meteo forecast service, with MET Norway as a "
        "fallback, for the farmer's coordinates."
    )

    if msp.counts().get("msp_commodities"):
        sources.append(
            "Support prices: the Minimum Support Prices announced by the "
            "Government of India for the marketing year, as published by "
            "Agmarknet."
        )
    if Mandi_Price_Tool.is_available():
        sources.append(
            "Market prices: daily mandi prices published by the Government of "
            "India on data.gov.in."
        )

    schemes = _scheme_names()
    if schemes:
        sources.append(
            "Schemes: official guidance documents where available, and "
            "publicly available reference material otherwise. The answer says "
            "which kind it used."
        )

    provider = "Google Maps" if config.LOCATION_API_KEY else "OpenStreetMap"
    sources.append(
        f"Location: the place the farmer named, converted to coordinates using "
        f"{provider}."
    )

    return sources


def profile_summary(profile: dict | None) -> str:
    """What is currently held about this particular farmer."""
    if not profile:
        return "Nothing is stored about this farmer yet."

    bits = []
    if profile.get("location_text"):
        where = profile["location_text"]
        if profile.get("latitude") is not None:
            where += f" (coordinates {profile['latitude']:.2f}, {profile['longitude']:.2f})"
        bits.append(f"location: {where}")
    if profile.get("state"):
        bits.append(f"state: {profile['state']}")
    if profile.get("crops"):
        bits.append(f"crops mentioned: {', '.join(profile['crops'])}")

    if not bits:
        return "Nothing is stored about this farmer yet."
    return "Stored for this farmer - " + "; ".join(bits)


def describe(profile: dict | None = None) -> str:
    """The full factual briefing used to answer questions about the service."""
    lines = [
        "AnnaData is an agricultural advisory service for Indian farmers, "
        "reachable by SMS and on the web.",
        "",
        "WHAT IT CAN HELP WITH:",
    ]
    lines += [f"- {c}" for c in capabilities()]
    lines += ["", "WHERE ITS INFORMATION COMES FROM:"]
    lines += [f"- {s}" for s in data_sources()]
    lines += ["", profile_summary(profile)]
    return "\n".join(lines)
