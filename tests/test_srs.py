"""Spaced repetition: "again" resets the word's progress, and a word counts once per day."""
import os

os.environ.setdefault("SEED_ADMIN_PASSWORD", "test-admin-pw")

from app import create_app          # noqa: E402
from lib import srs                 # noqa: E402
from lib.db import connect, today   # noqa: E402


def _account(username="srsuser"):
    admin = create_app().test_client()
    admin.post("/api/auth/login", json={"username": "admin", "password": "test-admin-pw"})
    admin.post("/api/admin/users",
               json={"username": username, "password": "pass1234",
                     "direction": "General / 综合"})
    c = connect()
    uid = c.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()["id"]
    c.execute("INSERT OR IGNORE INTO words(id,account_id,word,tier,gloss_zh,status) "
              "VALUES(90001,?,?,'D1','测试词','todo')", (uid, "eutrophication"))
    c.commit()
    return c, uid


def _review(c, uid):
    return c.execute("SELECT * FROM reviews WHERE word_id=90001").fetchone()


def _log(c, uid):
    return c.execute("SELECT * FROM log WHERE account_id=? AND day=?",
                     (uid, today())).fetchone()


def test_again_clears_progress_even_after_a_good_answer():
    c, uid = _account()
    srs.apply(c, uid, 90001, srs.GRADE_GOOD)
    assert _review(c, uid)["reps"] == 1

    # 本组里第二遍点了「不认识」：即使这个词今天已经答对过，也必须清零
    state = srs.apply(c, uid, 90001, srs.GRADE_AGAIN)
    assert state["reps"] == 0
    assert state["interval"] == 1                 # 明天从头再来
    assert state["status"] == "learning"
    assert _review(c, uid)["lapses"] == 1


def test_repeat_answers_on_the_same_day_are_counted_once():
    c, uid = _account()
    srs.apply(c, uid, 90001, srs.GRADE_GOOD)
    first = dict(_log(c, uid))
    srs.apply(c, uid, 90001, srs.GRADE_AGAIN)
    srs.apply(c, uid, 90001, srs.GRADE_AGAIN)
    assert dict(_log(c, uid)) == first
