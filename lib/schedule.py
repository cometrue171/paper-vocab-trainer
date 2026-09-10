"""12-week study rhythm stats — all scoped to one account."""
import sqlite3
from datetime import date, timedelta

from .db import get_setting, today

MILESTONES = [
    "第1-4周 · 领域核心词 + 学术高频词：能看懂标题与摘要大意",
    "第5-8周 · 精读 5-8 篇典型论文，语境词累积：方法段落可较流畅阅读",
    "第9-12周 · 全生命周期 + 建模/数据类词汇（为大数据方向铺路）：全文可不太查词通读",
]


def current_week(conn: sqlite3.Connection, account_id: int) -> int:
    start = get_setting(conn, account_id, "start_day") or today()
    try:
        s = date.fromisoformat(start)
    except ValueError:
        return 1
    return max(1, (date.today() - s).days // 7 + 1)


def phase(conn: sqlite3.Connection, account_id: int) -> dict:
    wk = current_week(conn, account_id)
    idx = 0 if wk <= 4 else (1 if wk <= 8 else 2)
    return {"week": wk, "phase_index": idx, "phase_text": MILESTONES[idx],
            "all_phases": MILESTONES}


def recommend_new(conn: sqlite3.Connection, account_id: int) -> int:
    wk = current_week(conn, account_id)
    ramp = {0: 15, 1: 15, 2: 15, 3: 15, 4: 15, 5: 18,
            6: 18, 7: 18, 8: 20, 9: 20, 10: 20, 11: 20}
    return ramp.get(min(wk, 11), 20)


def streak(conn: sqlite3.Connection, account_id: int) -> int:
    rows = [r[0] for r in conn.execute(
        "SELECT day FROM log WHERE account_id=? ORDER BY day DESC",
        (account_id,)).fetchall()]
    if not rows:
        return 0
    active = set(rows)
    cur = date.today()
    if cur.isoformat() not in active:
        cur -= timedelta(days=1)
    n = 0
    while cur.isoformat() in active:
        n += 1
        cur -= timedelta(days=1)
    return n


def activity(conn: sqlite3.Connection, account_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT day, SUM(new_done) n FROM log WHERE account_id=? GROUP BY day",
        (account_id,)).fetchall()
    start = get_setting(conn, account_id, "start_day") or today()
    try:
        start_d = date.fromisoformat(start)
    except ValueError:
        start_d = date.today()
    by_week: dict[int, int] = {}
    for r in rows:
        try:
            d = date.fromisoformat(r["day"])
        except ValueError:
            continue
        wk = (d - start_d).days // 7
        by_week[wk] = by_week.get(wk, 0) + (r["n"] or 0)
    out = []
    today_week = (date.today() - start_d).days // 7
    for wk in range(max(0, today_week - 7), today_week + 1):
        out.append({"week": wk + 1, "new_done": by_week.get(wk, 0)})
    return out


def log_today(conn: sqlite3.Connection, account_id: int) -> dict:
    row = conn.execute(
        "SELECT * FROM log WHERE account_id=? AND day=?", (account_id, today())
    ).fetchone()
    return dict(row) if row else {"new_done": 0, "review_done": 0, "sent_done": 0}
