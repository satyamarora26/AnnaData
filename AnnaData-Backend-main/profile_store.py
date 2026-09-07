"""
Farmer profiles and conversation history.

Two kinds of memory live here, and they deliberately have different lifetimes:

  profile facts  - location, crops, language. Persist indefinitely; a farmer in
                   Vidarbha growing cotton is still there next month.
  session context - the last few messages, so follow-ups make sense. Expires
                   after CONTEXT_TTL_HOURS so a conversation from three weeks
                   ago cannot bleed into today's question.

Every function degrades to a no-op when the database is unavailable, so the
agent keeps working exactly as it did before profiles existed.
"""
import json
from datetime import datetime, timedelta, timezone

import db
from config import CONTEXT_MESSAGES, CONTEXT_TTL_HOURS, LOCATION_ASK_COOLDOWN_HOURS


# A crop is one thing however it is written. Storing what the parser happened
# to return left 'wheat' and 'Wheat' as separate crops, and 'Kharif rice'
# alongside 'rice' - a season is not part of a crop's name.
SEASON_WORDS = ("kharif", "rabi", "zaid", "summer", "winter", "monsoon")


# Whether a name is a crop at all is a question the dose register can answer:
# it lists 314 of them. Without this check the profile accumulated whatever the
# parser returned, and one farmer ended up recorded as growing "small plant",
# "flower", "indoor potted plant" and "indoor potted plants" - the last two the
# same non-crop stored twice. The greeting then offered help with their
# "watermelons and plants", and an acknowledgement described five acres of
# indoor potted plants.
_KNOWN_CROPS: set[str] | None = None


def _known_crops() -> set[str]:
    """Every crop name the system recognises, loaded once."""
    global _KNOWN_CROPS
    if _KNOWN_CROPS is not None:
        return _KNOWN_CROPS

    import knowledge
    names = set(knowledge.CROP_SYNONYMS) | set(knowledge.CROP_SYNONYMS.values())
    try:
        with db.connection() as conn:
            names |= {r[0].strip().lower()
                      for r in conn.execute("SELECT DISTINCT crop FROM pesticide_uses").fetchall()
                      if r[0]}
    except Exception as e:
        # The synonym list alone still covers the common crops.
        print(f"Could not load crop names from the dose table: {e}")
    _KNOWN_CROPS = names
    return names


def _clean_crop(value: str | None) -> str | None:
    """Normalise a crop name, and reject anything that is not a crop."""
    crop = _clean(value)
    if not crop:
        return None
    words = [w for w in crop.lower().split() if w not in SEASON_WORDS]
    crop = " ".join(words).strip()
    if not crop:
        return None

    known = _known_crops()
    if crop in known:
        return crop
    # "tomatoes" and "indoor potted plants" both arrive plural; only one of them
    # is a crop, and the singular decides which.
    for plural, singular in (("ies", "y"), ("es", ""), ("s", "")):
        if crop.endswith(plural):
            candidate = crop[: -len(plural)] + singular
            if candidate in known:
                return candidate
    print(f"Not storing {crop!r} as a crop: not a name the system recognises")
    return None


def _clean(value: str | None) -> str | None:
    """Normalise the parser's 'unknown' sentinel to a real absence."""
    if not value:
        return None
    value = value.strip()
    if not value or value.lower() == "unknown":
        return None
    return value


def get_profile(user_id: str) -> dict | None:
    """Stored facts for a farmer, or None if unknown / no database."""
    if not user_id or not db.is_available():
        return None
    try:
        with db.connection() as conn:
            row = conn.execute(
                """
                SELECT user_id, location_text, latitude, longitude, state,
                       district, language, crops, location_asked_at
                  FROM farmers WHERE user_id = %s
                """,
                (user_id,),
            ).fetchone()
        if not row:
            return None
        return {
            "user_id": row[0],
            "location_text": row[1],
            "latitude": row[2],
            "longitude": row[3],
            "state": row[4],
            "district": row[5],
            "language": row[6],
            "crops": list(row[7] or []),
            "location_asked_at": row[8],
        }
    except Exception as e:
        print(f"Profile lookup failed for {user_id}: {e}")
        return None


def remember(
    user_id: str,
    channel: str = "sms",
    location_text: str | None = None,
    latitude: float | None = None,
    longitude: float | None = None,
    state: str | None = None,
    crop: str | None = None,
    language: str | None = None,
) -> None:
    """Merge newly learned facts into a farmer's profile.

    Only non-empty values overwrite; a message that mentions no location must
    not erase the location we learned last week. Crops accumulate rather than
    replace, since a farmer grows more than one thing.
    """
    if not user_id or not db.is_available():
        return

    location_text = _clean(location_text)
    state = _clean(state)
    crop = _clean_crop(crop)
    language = _clean(language)

    try:
        with db.connection() as conn:
            # Scalar facts: COALESCE keeps the stored value whenever this
            # message carried nothing new for that field.
            conn.execute(
                """
                INSERT INTO farmers (user_id, channel, location_text, latitude,
                                     longitude, state, language)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET
                    location_text = COALESCE(EXCLUDED.location_text, farmers.location_text),
                    latitude      = COALESCE(EXCLUDED.latitude,      farmers.latitude),
                    longitude     = COALESCE(EXCLUDED.longitude,     farmers.longitude),
                    state         = COALESCE(EXCLUDED.state,         farmers.state),
                    language      = COALESCE(EXCLUDED.language,      farmers.language),
                    updated_at    = now()
                """,
                (user_id, channel, location_text, latitude, longitude, state, language),
            )

            # Crops accumulate. Kept as its own statement because inferring the
            # parameter type inside a CASE on the upsert is more trouble than
            # it is worth, and this reads far better.
            if crop:
                conn.execute(
                    """
                    UPDATE farmers
                       SET crops = array_append(crops, %s), updated_at = now()
                     WHERE user_id = %s
                       AND NOT (lower(%s) = ANY(SELECT lower(unnest(crops))))
                    """,
                    (crop, user_id, crop),
                )
    except Exception as e:
        print(f"Profile update failed for {user_id}: {e}")


def recent_history(user_id: str) -> list[dict]:
    """Recent turns as [{'role': ..., 'content': ...}], oldest first.

    Bounded by both count and age: an old thread should not be treated as
    context for a question asked weeks later.
    """
    if not user_id or not db.is_available():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(hours=CONTEXT_TTL_HOURS)
    try:
        with db.connection() as conn:
            rows = conn.execute(
                """
                SELECT direction, body FROM messages
                 WHERE user_id = %s AND created_at >= %s
              ORDER BY created_at DESC, id DESC
                 LIMIT %s
                """,
                (user_id, cutoff, CONTEXT_MESSAGES),
            ).fetchall()
    except Exception as e:
        print(f"History lookup failed for {user_id}: {e}")
        return []

    rows.reverse()
    return [
        {"role": "user" if d == "inbound" else "assistant", "content": b}
        for d, b in rows
    ]


def log_message(
    user_id: str,
    direction: str,
    body: str,
    gateway_message_id: str | None = None,
    meta: dict | None = None,
    session_id: int | None = None,
) -> None:
    """Record one message. Duplicate gateway ids are ignored, not an error."""
    if not user_id or not db.is_available() or not body:
        return
    try:
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO messages (user_id, direction, body, gateway_message_id,
                                      meta, session_id)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (gateway_message_id) DO NOTHING
                """,
                (user_id, direction, body, gateway_message_id,
                 json.dumps(meta) if meta else None, session_id),
            )
    except Exception as e:
        print(f"Message log failed for {user_id}: {e}")


def should_ask_location(profile: dict | None) -> bool:
    """Whether to append the location invitation to this reply.

    Only when we genuinely have no coordinates, and not more than once a day -
    a farmer who ignores the question should not be nagged on every message.
    """
    if not db.is_available():
        return False
    if profile is None:
        return True
    if profile.get("latitude") is not None and profile.get("longitude") is not None:
        return False

    asked = profile.get("location_asked_at")
    if asked is None:
        return True
    if asked.tzinfo is None:
        asked = asked.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - asked > timedelta(hours=LOCATION_ASK_COOLDOWN_HOURS)


def mark_location_asked(user_id: str) -> None:
    if not user_id or not db.is_available():
        return
    try:
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO farmers (user_id, location_asked_at)
                VALUES (%s, now())
                ON CONFLICT (user_id) DO UPDATE
                    SET location_asked_at = now(), updated_at = now()
                """,
                (user_id,),
            )
    except Exception as e:
        print(f"Could not record location prompt for {user_id}: {e}")


def forget(user_id: str) -> bool:
    """Delete a farmer's profile and messages. Backs the STOP keyword."""
    if not user_id or not db.is_available():
        return False
    try:
        with db.connection() as conn:
            conn.execute("DELETE FROM messages WHERE user_id = %s", (user_id,))
            conn.execute("DELETE FROM farmers  WHERE user_id = %s", (user_id,))
        print(f"Erased profile and history for {user_id}")
        return True
    except Exception as e:
        print(f"Erase failed for {user_id}: {e}")
        return False
