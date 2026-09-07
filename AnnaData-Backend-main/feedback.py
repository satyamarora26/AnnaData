"""
Asking farmers what they thought, and storing it usefully.

A rating on its own tells you a farmer was unhappy. A rating stored beside what
the conversation was about - the intent, which tools ran, the language, the
crop, whether a dose was given or refused - tells you *what kind* of question
goes wrong, which is the thing that can be acted on. So the features a model
would eventually need are recorded at the same time as the number, because they
cannot be reconstructed afterwards.

Timing follows two rules that have to be reconciled. A rating should be asked
while the conversation is fresh, which means shortly after it ends; and it
should be asked at most once a month, on a day drawn per farmer rather than a
shared one. Those conflict if the drawn day is treated as the day to send - a
farmer whose last conversation was two weeks earlier is being asked about
something they no longer remember. So the drawn day is the *earliest eligible*
day, and the ask goes out after the first conversation that ends on or after
it.
"""
import json
import random
import re
from datetime import datetime, timedelta, timezone

import db
from config import (
    SESSION_GAP_HOURS,
    FEEDBACK_COOLDOWN_DAYS,
    FEEDBACK_WINDOW_HOURS,
)

SCHEMA = """
ALTER TABLE messages   ADD COLUMN IF NOT EXISTS session_id BIGINT;
ALTER TABLE farmers    ADD COLUMN IF NOT EXISTS feedback_day INTEGER;
ALTER TABLE farmers    ADD COLUMN IF NOT EXISTS feedback_asked_at TIMESTAMPTZ;

CREATE TABLE IF NOT EXISTS feedback (
    id           BIGSERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL,
    session_id   BIGINT,
    rating       INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    raw_message  TEXT,
    -- What the rated conversation actually consisted of. Kept so a rating can
    -- be attributed to a kind of question rather than only to a farmer.
    features     JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS feedback_user ON feedback (user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS messages_session ON messages (session_id);
"""

# A farmer asked to rate out of five may reply "4", "4/5", "chaar", "bahut
# accha 5". The number is what is stored; anything else they write is kept
# alongside it rather than parsed.
RATING_RE = re.compile(r"(?<![\d/])([1-5])(?![\d])")
# Beyond this length a bare digit is far more likely to be part of a question
# than a rating.
BARE_NUMBER_MAX_CHARS = 25

WORD_RATINGS = {
    "one": 1, "ek": 1, "two": 2, "do": 2, "three": 3, "teen": 3, "tin": 3,
    "four": 4, "char": 4, "chaar": 4, "five": 5, "paanch": 5, "panch": 5,
}


def init() -> bool:
    if not db.is_available():
        return False
    try:
        with db.connection() as conn:
            conn.execute(SCHEMA)
        return True
    except Exception as e:
        print(f"Feedback store unavailable: {e}")
        return False


# --- sessions ---------------------------------------------------------------

def current_session(user_id: str) -> int:
    """The session this message belongs to.

    A gap longer than SESSION_GAP_HOURS starts a new one, which is the same
    boundary used to decide a conversation has finished.
    """
    if not db.is_available():
        return 0
    try:
        with db.connection() as conn:
            row = conn.execute(
                """
                SELECT session_id, created_at FROM messages
                 WHERE user_id = %s AND session_id IS NOT NULL
              ORDER BY created_at DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()

            if row and row[0] is not None:
                age = datetime.now(timezone.utc) - row[1]
                if age < timedelta(hours=SESSION_GAP_HOURS):
                    return row[0]

            nxt = conn.execute(
                "SELECT COALESCE(MAX(session_id), 0) + 1 FROM messages"
            ).fetchone()[0]
            return nxt
    except Exception as e:
        print(f"Session lookup failed for {user_id}: {e}")
        return 0


# --- asking -----------------------------------------------------------------

def _ensure_feedback_day(conn, user_id: str) -> int:
    """The day of the month this farmer is eligible from, drawn once.

    Drawn per farmer rather than shared, so reminders spread across the month
    instead of putting every one of them through the handset on the same day.
    Capped at 28 so every month has the day.
    """
    row = conn.execute(
        "SELECT feedback_day FROM farmers WHERE user_id = %s", (user_id,)
    ).fetchone()
    if row and row[0]:
        return row[0]

    day = random.randint(1, 28)
    conn.execute(
        "UPDATE farmers SET feedback_day = %s WHERE user_id = %s", (day, user_id)
    )
    return day


def _replyable(user_id: str) -> bool:
    """Whether a rating request could ever reach this identifier.

    Bank alerts and marketing arrive from DLT sender IDs like "JR-JIOPAY-S",
    which cannot receive SMS. One such sender was written to the farmer table
    and then came up due on every scheduler run, since the ask was counted only
    when it succeeded. The bridge now drops these on arrival; this is the second
    guard, for rows already stored.
    """
    digits = re.sub(r"[\s()\-.]", "", user_id or "")
    if digits.startswith("+"):
        digits = digits[1:]
    return digits.isdigit() and len(digits) >= 8


def due_for_feedback() -> list[str]:
    """Farmers whose conversation has finished and who are due to be asked."""
    if not db.is_available():
        return []

    now = datetime.now(timezone.utc)
    quiet_since = now - timedelta(hours=SESSION_GAP_HOURS)
    cooldown = now - timedelta(days=FEEDBACK_COOLDOWN_DAYS)

    try:
        with db.connection() as conn:
            rows = conn.execute(
                """
                SELECT f.user_id, f.feedback_day, MAX(m.created_at) AS last_seen
                  FROM farmers f
                  JOIN messages m ON m.user_id = f.user_id
                 WHERE (f.feedback_asked_at IS NULL OR f.feedback_asked_at < %s)
                   AND NOT EXISTS (SELECT 1 FROM feedback fb
                                    WHERE fb.user_id = f.user_id
                                      AND fb.created_at >= %s)
              GROUP BY f.user_id, f.feedback_day
                HAVING MAX(m.created_at) < %s
                """,
                (cooldown, cooldown, quiet_since),
            ).fetchall()

            due = []
            for user_id, day, _last_seen in rows:
                if not _replyable(user_id):
                    continue
                day = day or _ensure_feedback_day(conn, user_id)
                # The drawn day is the earliest eligible date, not the only one:
                # the ask waits for a conversation to finish on or after it.
                if now.day >= day:
                    due.append(user_id)
            return due
    except Exception as e:
        print(f"Feedback due-check failed: {e}")
        return []


def mark_asked(user_id: str) -> None:
    if not db.is_available():
        return
    try:
        with db.connection() as conn:
            conn.execute(
                "UPDATE farmers SET feedback_asked_at = now() WHERE user_id = %s",
                (user_id,),
            )
    except Exception as e:
        print(f"Could not record feedback prompt for {user_id}: {e}")


def awaiting_reply(user_id: str) -> bool:
    """Whether a recent ask is still unanswered, so the next message may be a rating.

    Answered by comparing the ask against the ratings, rather than by clearing
    the ask. The two facts - "a reply is pending" and "when this farmer was last
    asked" - were once the same column, so recording a rating erased the record
    of having asked, and the farmer became due again immediately: one was rated
    at 12:01 and asked a second time at 12:02.
    """
    if not db.is_available():
        return False
    try:
        with db.connection() as conn:
            row = conn.execute(
                """
                SELECT f.feedback_asked_at,
                       (SELECT max(created_at) FROM feedback
                         WHERE user_id = f.user_id) AS last_rated
                  FROM farmers f WHERE f.user_id = %s
                """,
                (user_id,),
            ).fetchone()
    except Exception:
        return False

    if not row or not row[0]:
        return False
    asked, rated = row[0], row[1]
    if asked.tzinfo is None:
        asked = asked.replace(tzinfo=timezone.utc)
    if rated is not None:
        if rated.tzinfo is None:
            rated = rated.replace(tzinfo=timezone.utc)
        if rated >= asked:
            return False          # this ask has already been answered
    return datetime.now(timezone.utc) - asked < timedelta(hours=FEEDBACK_WINDOW_HOURS)


# --- recording --------------------------------------------------------------

def parse_rating(message: str) -> int | None:
    """The rating in a reply, or None if there is not one.

    The farmer may write anything around it; only the number is kept. A message
    with no number in range is not a rating - it is a question, and is answered
    as one.
    """
    if not message:
        return None

    text = message.strip().lower()

    # An explicit rating is unambiguous however long the message is.
    for pattern in (
        r"\b([1-5])\s*(?:/|out of)\s*5\b",          # 4/5, 4 out of 5
        r"\b(?:rating|rate|stars?)\D{0,6}([1-5])\b",  # rating 4
        r"\b([1-5])\D{0,6}stars?\b",                 # 4 star
    ):
        found = re.search(pattern, text)
        if found:
            return int(found.group(1))

    # A bare number only counts in a short reply. "My 5 acre farm has bollworm"
    # is a question that happens to contain a digit, and scoring it as a rating
    # would both record nonsense and leave the farmer's problem unanswered.
    if len(text) > BARE_NUMBER_MAX_CHARS:
        return None

    match = RATING_RE.search(text)
    if match:
        return int(match.group(1))

    for word, value in WORD_RATINGS.items():
        if re.search(rf"\b{word}\b", text):
            return value
    return None


def session_features(user_id: str, session_id: int) -> dict:
    """What the rated conversation consisted of.

    Recorded with the rating because it cannot be reconstructed later, and
    because a number without it says a farmer was unhappy without saying what
    about.
    """
    if not db.is_available():
        return {}
    try:
        with db.connection() as conn:
            rows = conn.execute(
                """
                SELECT direction, meta FROM messages
                 WHERE user_id = %s AND session_id = %s
                """,
                (user_id, session_id),
            ).fetchall()
            profile = conn.execute(
                "SELECT state, crops, location_text FROM farmers WHERE user_id = %s",
                (user_id,),
            ).fetchone()
    except Exception as e:
        print(f"Could not gather session features for {user_id}: {e}")
        return {}

    intents, tools = [], []
    inbound = outbound = 0
    for direction, meta in rows:
        if direction == "inbound":
            inbound += 1
        else:
            outbound += 1
        if meta:
            data = meta if isinstance(meta, dict) else json.loads(meta)
            if data.get("intent"):
                intents.append(data["intent"])
            tools.extend(data.get("tools") or [])

    return {
        "turns_in": inbound,
        "turns_out": outbound,
        "intents": sorted(set(intents)),
        "tools": sorted(set(tools)),
        "used_doses": "doses" in tools,
        "used_kb": "kb" in tools,
        "state": profile[0] if profile else None,
        "crops": list(profile[1]) if profile and profile[1] else [],
        "location": profile[2] if profile else None,
    }


def record(user_id: str, rating: int, raw_message: str, session_id: int | None) -> bool:
    """Store a rating with the features of the conversation it refers to."""
    if not db.is_available() or not (1 <= rating <= 5):
        return False
    try:
        features = session_features(user_id, session_id) if session_id else {}
        with db.connection() as conn:
            conn.execute(
                """
                INSERT INTO feedback (user_id, session_id, rating, raw_message, features)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (user_id, session_id, rating, raw_message[:500], json.dumps(features)),
            )
        print(f"Recorded rating {rating} from {user_id}")
        return True
    except Exception as e:
        print(f"Could not record rating for {user_id}: {e}")
        return False


def summary() -> dict:
    """Ratings so far, and how they break down by what was asked."""
    if not db.is_available():
        return {}
    try:
        with db.connection() as conn:
            total, mean = conn.execute(
                "SELECT count(*), round(avg(rating), 2) FROM feedback"
            ).fetchone()
            by_intent = conn.execute(
                """
                SELECT intent, count(*), round(avg(rating), 2)
                  FROM feedback, jsonb_array_elements_text(features->'intents') AS intent
              GROUP BY intent ORDER BY avg(rating) ASC
                """
            ).fetchall()
            distribution = conn.execute(
                "SELECT rating, count(*) FROM feedback GROUP BY rating ORDER BY rating"
            ).fetchall()
        return {
            "total": total,
            "mean": float(mean) if mean is not None else None,
            "distribution": {r: n for r, n in distribution},
            "worst_intents": [
                {"intent": i, "n": n, "mean": float(a)} for i, n, a in by_intent
            ],
        }
    except Exception as e:
        print(f"Feedback summary failed: {e}")
        return {}
