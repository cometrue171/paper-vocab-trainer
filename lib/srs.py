"""SM-2 style spaced repetition over a single account's word pool."""
import sqlite3
from datetime import date, timedelta

from .db import get_setting, set_setting, today

GRADE_AGAIN = 0
GRADE_HARD = 1
GRADE_GOOD = 2


def _shift_days(n: int) -> str:
    return (date.today() + timedelta(days=n)).isoformat()


def ensure_start_day(conn: sqlite3.Connection, account_id: int) -> None:
    if not get_setting(conn, account_id, "start_day"):
        set_setting(conn, account_id, "start_day", today())


def new_queue(conn: sqlite3.Connection, account_id: int,
              limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT w.* FROM words w "
        "WHERE w.account_id=? AND w.status='todo' AND w.known=0 "
        "ORDER BY w.is_domain DESC, "
        "  CASE w.tier WHEN 'D0' THEN 0 WHEN 'D1' THEN 1 ELSE 2 END, "
        "  w.freq DESC, w.id "
        "LIMIT ?", (account_id, limit)).fetchall()


def due_queue(conn: sqlite3.Connection, account_id: int,
              cap: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT w.*, r.due FROM reviews r "
        "JOIN words w ON w.id=r.word_id "
        "WHERE w.account_id=? AND w.known=0 AND r.due<=? "
        "ORDER BY r.due ASC, w.id LIMIT ?",
        (account_id, today(), cap)).fetchall()


def apply(conn: sqlite3.Connection, account_id: int, word_id: int,
          grade: int) -> dict:
    ensure_start_day(conn, account_id)
    wstate = conn.execute(
        "SELECT status FROM words WHERE id=? AND account_id=?",
        (word_id, account_id)).fetchone()
    if not wstate:
        raise ValueError("该词不属于当前账号")
    first_study = wstate["status"] == "todo"
    row = conn.execute("SELECT * FROM reviews WHERE word_id=?", (word_id,)).fetchone()
    reps = row["reps"] if row else 0
    interval = row["interval"] if row else 0
    ease = row["ease"] if row else 2.5
    lapses = row["lapses"] if row else 0

    if grade == GRADE_AGAIN:
        lapses += 1
        interval = 1
        ease = max(1.3, ease - 0.2)
        reps = 0
    elif grade == GRADE_HARD:
        reps += 1
        interval = max(1, int(interval * 0.5)) if reps > 1 else 1
        ease = max(1.3, ease - 0.1)
    else:  # good
        reps += 1
        if interval == 0:
            interval = 1
        elif interval == 1:
            interval = 3
        elif interval == 3:
            interval = 7
        else:
            interval = max(interval + 1, int(interval * ease))
        ease = min(2.8, ease + 0.02)

    due = _shift_days(interval)
    conn.execute(
        "INSERT INTO reviews(word_id,due,interval,ease,reps,lapses,last_reviewed) "
        "VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(word_id) DO UPDATE SET due=excluded.due, "
        "interval=excluded.interval, ease=excluded.ease, reps=excluded.reps, "
        "lapses=excluded.lapses, last_reviewed=excluded.last_reviewed",
        (word_id, due, interval, ease, reps, lapses, today()),
    )
    status = "mastered" if (reps >= 5 and interval >= 21) else "learning"
    conn.execute("UPDATE words SET status=? WHERE id=?", (status, word_id))

    _bump_log(conn, account_id,
              new_done=int(first_study), review_done=int(not first_study))
    conn.commit()
    return {"due": due, "interval": interval, "ease": ease, "reps": reps,
            "status": status}


def mark_known(conn: sqlite3.Connection, account_id: int, word_id: int) -> None:
    conn.execute(
        "UPDATE words SET known=1, status='mastered' WHERE id=? AND account_id=?",
        (word_id, account_id))
    conn.execute("DELETE FROM reviews WHERE word_id=?", (word_id,))
    conn.commit()


def _bump_log(conn: sqlite3.Connection, account_id: int,
              new_done: int = 0, review_done: int = 0) -> None:
    conn.execute(
        "INSERT INTO log(account_id,day,new_done,review_done,sent_done) "
        "VALUES(?,?,?,?,0) ON CONFLICT(account_id,day) DO UPDATE SET "
        "new_done=new_done+excluded.new_done, "
        "review_done=review_done+excluded.review_done",
        (account_id, today(), new_done, review_done))


def due_today_count(conn: sqlite3.Connection, account_id: int) -> int:
    return conn.execute(
        "SELECT COUNT(*) c FROM reviews r "
        "JOIN words w ON w.id=r.word_id "
        "WHERE w.account_id=? AND w.known=0 AND r.due<=?",
        (account_id, today())).fetchone()["c"]
