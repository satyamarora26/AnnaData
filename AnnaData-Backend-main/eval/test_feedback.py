"""
Feedback timing, checked against the database rather than the model.

The rating flow has no language in it - it is scheduling logic - so the case
that broke it could not be expressed in cases.yaml. A farmer was asked to rate,
did so at 12:01, and was asked again at 12:02, because recording a rating
cleared the same column that recorded when they were last asked.

Run: python eval/test_feedback.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db
import feedback
from config import FEEDBACK_COOLDOWN_DAYS

USER = "+919999000001"          # reserved for this test, removed afterwards
failures = []


def check(name: str, got, want):
    ok = got == want
    print(f"  {'pass' if ok else 'FAIL'}   {name}" + ("" if ok else f"  (got {got!r}, want {want!r})"))
    if not ok:
        failures.append(name)


def cleanup(conn):
    for table in ("feedback", "messages", "farmers"):
        conn.execute(f"DELETE FROM {table} WHERE user_id = %s", (USER,))


def main() -> int:
    if not db.init() or not feedback.init():
        print("Database unavailable; cannot run feedback tests")
        return 2

    now = datetime.now(timezone.utc)
    with db.connection() as conn:
        cleanup(conn)
        # A farmer whose conversation finished well before the quiet threshold,
        # eligible from the 1st so the drawn day never gates the test.
        conn.execute(
            "INSERT INTO farmers (user_id, feedback_day) VALUES (%s, 1)", (USER,)
        )
        conn.execute(
            """INSERT INTO messages (user_id, direction, body, created_at)
               VALUES (%s, 'inbound', 'kapas me sundi', %s)""",
            (USER, now - timedelta(hours=6)),
        )

    try:
        check("due before being asked", USER in feedback.due_for_feedback(), True)

        feedback.mark_asked(USER)
        check("a reply is pending once asked", feedback.awaiting_reply(USER), True)
        check("not due again while the ask is open",
              USER in feedback.due_for_feedback(), False)

        feedback.record(USER, 5, "5", session_id=None)
        # The bug: recording the rating cleared the record of having asked, so
        # the next scheduler run asked again a minute later.
        check("no reply pending after rating", feedback.awaiting_reply(USER), False)
        check("NOT due again straight after rating",
              USER in feedback.due_for_feedback(), False)

        # And the monthly cooldown still has to be enforced from the ask.
        with db.connection() as conn:
            conn.execute(
                "UPDATE farmers SET feedback_asked_at = %s WHERE user_id = %s",
                (now - timedelta(days=FEEDBACK_COOLDOWN_DAYS + 1), USER),
            )
            conn.execute("DELETE FROM feedback WHERE user_id = %s", (USER,))
        check("due again once the cooldown has passed",
              USER in feedback.due_for_feedback(), True)
    finally:
        with db.connection() as conn:
            cleanup(conn)

    print(f"\n{6 - len(failures)} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
