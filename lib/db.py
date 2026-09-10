"""SQLite access layer — multi-account aware.

Data isolation model
--------------------
User-owned rows carry an `account_id`:
  papers, words, log, settings
Descendant tables (contexts -> papers, reviews -> words, sent_notes -> contexts)
are scoped through their parent, so they need no extra column.

dict / ipa are global read-only dictionaries shared by all accounts.
"""
import os
import sqlite3
from datetime import date
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

from .paths import DB_PATH, ensure_dirs

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY,
    username      TEXT NOT NULL COLLATE NOCASE UNIQUE,
    password_hash TEXT NOT NULL,
    is_admin      INTEGER NOT NULL DEFAULT 0,
    direction     TEXT NOT NULL DEFAULT '',
    note          TEXT NOT NULL DEFAULT '',
    created_at    TEXT NOT NULL DEFAULT (date('now','localtime'))
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS papers (
    id           INTEGER PRIMARY KEY,
    account_id   INTEGER NOT NULL DEFAULT 1,
    doi          TEXT NOT NULL DEFAULT '',
    title        TEXT NOT NULL DEFAULT '',
    abstract     TEXT NOT NULL DEFAULT '',
    year         INTEGER,
    source       TEXT NOT NULL DEFAULT '',
    url          TEXT NOT NULL DEFAULT '',
    oa           INTEGER NOT NULL DEFAULT 0,
    has_fulltext INTEGER NOT NULL DEFAULT 0,
    fulltext_file TEXT NOT NULL DEFAULT '',
    oa_pdf       TEXT NOT NULL DEFAULT '',
    extracted    INTEGER NOT NULL DEFAULT 0,
    is_reading   INTEGER NOT NULL DEFAULT 0,
    added_at     TEXT NOT NULL DEFAULT (date('now','localtime')),
    UNIQUE (account_id, doi)
);

CREATE TABLE IF NOT EXISTS words (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL DEFAULT 1,
    word        TEXT NOT NULL,
    tier        TEXT NOT NULL DEFAULT 'D2',
    freq        INTEGER NOT NULL DEFAULT 0,
    n_papers    INTEGER NOT NULL DEFAULT 0,
    gloss_zh    TEXT NOT NULL DEFAULT '',
    phonetic    TEXT NOT NULL DEFAULT '',
    is_domain   INTEGER NOT NULL DEFAULT 0,
    known       INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'todo',
    created_at  TEXT NOT NULL DEFAULT (date('now','localtime')),
    UNIQUE (account_id, word COLLATE NOCASE)
);

CREATE TABLE IF NOT EXISTS contexts (
    id       INTEGER PRIMARY KEY,
    paper_id INTEGER NOT NULL,
    word_id  INTEGER NOT NULL,
    sentence TEXT NOT NULL,
    section  TEXT NOT NULL DEFAULT 'body',
    UNIQUE (word_id, paper_id, sentence)
);

CREATE TABLE IF NOT EXISTS reviews (
    word_id        INTEGER PRIMARY KEY,
    due            TEXT NOT NULL,
    interval       INTEGER NOT NULL DEFAULT 0,
    ease           REAL NOT NULL DEFAULT 2.5,
    reps           INTEGER NOT NULL DEFAULT 0,
    lapses         INTEGER NOT NULL DEFAULT 0,
    last_reviewed  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS log (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER NOT NULL DEFAULT 1,
    day         TEXT NOT NULL,
    new_done    INTEGER NOT NULL DEFAULT 0,
    review_done INTEGER NOT NULL DEFAULT 0,
    sent_done   INTEGER NOT NULL DEFAULT 0,
    UNIQUE (account_id, day)
);

CREATE TABLE IF NOT EXISTS sent_notes (
    id         INTEGER PRIMARY KEY,
    context_id INTEGER NOT NULL,
    my_trans   TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT (date('now','localtime'))
);

CREATE TABLE IF NOT EXISTS settings (
    account_id INTEGER NOT NULL DEFAULT 1,
    key        TEXT NOT NULL,
    value      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (account_id, key)
);

CREATE TABLE IF NOT EXISTS dict (
    word        TEXT PRIMARY KEY COLLATE NOCASE,
    phonetic    TEXT NOT NULL DEFAULT '',
    translation_zh TEXT NOT NULL DEFAULT '',
    tag         TEXT NOT NULL DEFAULT '',
    pos         TEXT NOT NULL DEFAULT '',
    definition  TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ipa (
    word  TEXT PRIMARY KEY COLLATE NOCASE,
    ipa   TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_contexts_word  ON contexts(word_id);
CREATE INDEX IF NOT EXISTS idx_contexts_paper ON contexts(paper_id);
"""

INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_words_acct_status ON words(account_id, status);
CREATE INDEX IF NOT EXISTS idx_words_acct_freq  ON words(account_id, freq DESC);
CREATE INDEX IF NOT EXISTS idx_papers_acct      ON papers(account_id);
CREATE INDEX IF NOT EXISTS idx_log_acct_day     ON log(account_id, day);
"""

DEFAULT_SETTINGS = {
    "daily_new": "15",
    "daily_sent": "2",
    "review_cap": "60",
    "port": "5010",
    "start_day": "",
    "trans_provider": "",
    "trans_key": "",
    "min_level": "cet4",            # 目标难度档 cet4/cet6/ky
    "seed_terms": "global phosphorus footprint|phosphorus life cycle assessment|anthropogenic phosphorus flows|global phosphorus cycle|phosphorus use efficiency|phosphorus recovery|eutrophication|phosphate rock",
    "oa_download_limit": "40",
}


def connect() -> sqlite3.Connection:
    ensure_dirs()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    ensure_dirs()
    with connect() as conn:
        conn.executescript(SCHEMA)
        migrate_legacy(conn)
        ensure_dict_pos(conn)
        create_indexes(conn)
        ensure_seed_admin(conn)


def ensure_columns(conn: sqlite3.Connection) -> None:
    """Compatibility: ensures schema is current for this connection."""
    migrate_legacy(conn)
    create_indexes(conn)


def ensure_dict_pos(conn: sqlite3.Connection) -> None:
    """Older databases predate dict.pos / dict.definition."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(dict)").fetchall()}
    if not cols:
        return
    added = False
    for col in ("pos", "definition"):
        if col not in cols:
            conn.execute(f"ALTER TABLE dict ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
            added = True
    if added:
        conn.commit()


def create_indexes(conn: sqlite3.Connection) -> None:
    conn.executescript(INDEX_SQL)


# --------------------------------------------------------------------------- #
# migration (legacy single-user DB -> multi-account)
# --------------------------------------------------------------------------- #
def migrate_legacy(conn: sqlite3.Connection) -> None:
    """If the DB predates account columns, rebuild the four keyed tables."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(words)").fetchall()}
    if "account_id" in cols:
        return  # already migrated / fresh
    conn.executescript("""
    CREATE TABLE words_new (
        id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL DEFAULT 1,
        word TEXT NOT NULL, tier TEXT NOT NULL DEFAULT 'D2',
        freq INTEGER NOT NULL DEFAULT 0, n_papers INTEGER NOT NULL DEFAULT 0,
        gloss_zh TEXT NOT NULL DEFAULT '', phonetic TEXT NOT NULL DEFAULT '',
        is_domain INTEGER NOT NULL DEFAULT 0, known INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'todo',
        created_at TEXT NOT NULL DEFAULT (date('now','localtime')),
        UNIQUE (account_id, word COLLATE NOCASE));
    INSERT INTO words_new(id,account_id,word,tier,freq,n_papers,gloss_zh,
        phonetic,is_domain,known,status,created_at)
        SELECT id,1,word,tier,freq,n_papers,gloss_zh,phonetic,is_domain,known,
               status,created_at FROM words;
    DROP TABLE words; ALTER TABLE words_new RENAME TO words;

    CREATE TABLE papers_new (
        id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL DEFAULT 1,
        doi TEXT NOT NULL DEFAULT '', title TEXT NOT NULL DEFAULT '',
        abstract TEXT NOT NULL DEFAULT '', year INTEGER,
        source TEXT NOT NULL DEFAULT '', url TEXT NOT NULL DEFAULT '',
        oa INTEGER NOT NULL DEFAULT 0, has_fulltext INTEGER NOT NULL DEFAULT 0,
        fulltext_file TEXT NOT NULL DEFAULT '', oa_pdf TEXT NOT NULL DEFAULT '',
        extracted INTEGER NOT NULL DEFAULT 0, is_reading INTEGER NOT NULL DEFAULT 0,
        added_at TEXT NOT NULL DEFAULT (date('now','localtime')),
        UNIQUE (account_id, doi));
    INSERT INTO papers_new(id,account_id,doi,title,abstract,year,source,url,oa,
        has_fulltext,fulltext_file,oa_pdf,extracted,is_reading,added_at)
        SELECT id,1,doi,title,abstract,year,source,url,oa,has_fulltext,
               fulltext_file,oa_pdf,extracted,is_reading,added_at FROM papers;
    DROP TABLE papers; ALTER TABLE papers_new RENAME TO papers;

    CREATE TABLE log_new (
        id INTEGER PRIMARY KEY, account_id INTEGER NOT NULL DEFAULT 1,
        day TEXT NOT NULL, new_done INTEGER NOT NULL DEFAULT 0,
        review_done INTEGER NOT NULL DEFAULT 0, sent_done INTEGER NOT NULL DEFAULT 0,
        UNIQUE (account_id, day));
    INSERT INTO log_new(id,account_id,day,new_done,review_done,sent_done)
        SELECT id,1,day,new_done,review_done,sent_done FROM log;
    DROP TABLE log; ALTER TABLE log_new RENAME TO log;

    ALTER TABLE settings RENAME TO settings_old;
    CREATE TABLE settings (
        account_id INTEGER NOT NULL DEFAULT 1,
        key TEXT NOT NULL, value TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (account_id, key));
    INSERT INTO settings(account_id,key,value) SELECT 1,key,value FROM settings_old;
    DROP TABLE settings_old;
    """)
    conn.commit()


# --------------------------------------------------------------------------- #
# users
# --------------------------------------------------------------------------- #
ADMIN_USERNAME = "admin"
ADMIN_DIRECTION = "General / 综合"


def ensure_seed_admin(conn: sqlite3.Connection) -> None:
    """Create the first admin account once.

    The password comes from SEED_ADMIN_PASSWORD; otherwise a random one is
    generated and printed, so no credential is ever baked into the source.
    """
    if conn.execute("SELECT 1 FROM users WHERE id=1").fetchone():
        return
    import secrets
    import sys
    pwd = os.environ.get("SEED_ADMIN_PASSWORD") or secrets.token_urlsafe(9)
    conn.execute(
        "INSERT INTO users(id,username,password_hash,is_admin,direction,note) "
        "VALUES(?,?,?,1,?,'system administrator')",
        (1, ADMIN_USERNAME, generate_password_hash(pwd), ADMIN_DIRECTION))
    conn.commit()
    if not os.environ.get("SEED_ADMIN_PASSWORD"):
        print(f"[init] created admin account '{ADMIN_USERNAME}' "
              f"with password: {pwd}\n"
              f"       change it after logging in, or set SEED_ADMIN_PASSWORD.",
              file=sys.stderr)


def user_row(conn: sqlite3.Connection, username: str | None = None,
             uid: int | None = None):
    if uid is not None:
        return conn.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
    return conn.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE",
                        (username,)).fetchone()


def list_users(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT u.id,u.username,u.is_admin,u.direction,u.note,u.created_at,"
        "(SELECT COUNT(*) FROM words w WHERE w.account_id=u.id) AS word_count "
        "FROM users u ORDER BY u.id").fetchall()


def create_user(conn: sqlite3.Connection, username: str, password: str,
                direction: str, is_admin: int = 0, note: str = "") -> int:
    if user_row(conn, username):
        raise ValueError(f"用户名 {username} 已存在")
    cur = conn.execute(
        "INSERT INTO users(username,password_hash,is_admin,direction,note) "
        "VALUES(?,?,?,?,?)",
        (username, generate_password_hash(password), int(is_admin),
         direction, note))
    conn.commit()
    return cur.lastrowid


def reset_password(conn: sqlite3.Connection, uid: int, new_password: str) -> None:
    conn.execute("UPDATE users SET password_hash=? WHERE id=?",
                 (generate_password_hash(new_password), uid))
    conn.commit()


def delete_user(conn: sqlite3.Connection, uid: int) -> None:
    """Delete an account and everything it owns."""
    u = user_row(conn, uid=uid)
    if not u or u["is_admin"]:
        raise ValueError("不能删除管理员账号")
    # sent_notes -> contexts -> papers of that account
    conn.execute(
        "DELETE FROM sent_notes WHERE context_id IN "
        "(SELECT c.id FROM contexts c JOIN papers p ON p.id=c.paper_id "
        "WHERE p.account_id=?)", (uid,))
    conn.execute(
        "DELETE FROM contexts WHERE paper_id IN "
        "(SELECT id FROM papers WHERE account_id=?)", (uid,))
    conn.execute(
        "DELETE FROM reviews WHERE word_id IN "
        "(SELECT id FROM words WHERE account_id=?)", (uid,))
    conn.execute("DELETE FROM words WHERE account_id=?", (uid,))
    conn.execute("DELETE FROM papers WHERE account_id=?", (uid,))
    conn.execute("DELETE FROM log WHERE account_id=?", (uid,))
    conn.execute("DELETE FROM settings WHERE account_id=?", (uid,))
    conn.execute("DELETE FROM users WHERE id=?", (uid,))
    conn.commit()


def check_login(conn: sqlite3.Connection, username: str, password: str):
    u = user_row(conn, username)
    if u and check_password_hash(u["password_hash"], password):
        return u
    return None


# --------------------------------------------------------------------------- #
# global meta / secret
# --------------------------------------------------------------------------- #
def get_meta(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    r = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute("INSERT INTO meta(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                 (key, value))
    conn.commit()


def get_secret(conn: sqlite3.Connection) -> str:
    s = get_meta(conn, "secret_key")
    if not s:
        s = os.urandom(24).hex()
        set_meta(conn, "secret_key", s)
    return s


# --------------------------------------------------------------------------- #
# settings (per account; falls back to built-in defaults)
# --------------------------------------------------------------------------- #
def get_setting(conn: sqlite3.Connection, account_id: int, key: str,
                default: str = "") -> str:
    if not default and key in DEFAULT_SETTINGS:
        default = DEFAULT_SETTINGS[key]
    r = conn.execute("SELECT value FROM settings WHERE account_id=? AND key=?",
                     (account_id, key)).fetchone()
    return r["value"] if r else default


def set_setting(conn: sqlite3.Connection, account_id: int, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(account_id,key,value) VALUES(?,?,?) "
        "ON CONFLICT(account_id,key) DO UPDATE SET value=excluded.value",
        (account_id, key, str(value)))


def today() -> str:
    return date.today().isoformat()


# --------------------------------------------------------------------------- #
# words / contexts
# --------------------------------------------------------------------------- #
def word_row(conn: sqlite3.Connection, account_id: int, word: str):
    return conn.execute(
        "SELECT * FROM words WHERE account_id=? AND word=? COLLATE NOCASE",
        (account_id, word)).fetchone()


def upsert_word(conn, account_id: int, word: str, tier: str, freq: int,
                n_papers: int, is_domain: bool, gloss: str = "",
                phonetic: str = "") -> int:
    row = conn.execute(
        "SELECT id FROM words WHERE account_id=? AND word=? COLLATE NOCASE",
        (account_id, word)).fetchone()
    if row:
        conn.execute(
            "UPDATE words SET freq=freq+?, n_papers=MAX(n_papers,?), "
            "gloss_zh=CASE WHEN gloss_zh='' THEN ? ELSE gloss_zh END, "
            "phonetic=CASE WHEN phonetic='' THEN ? ELSE phonetic END "
            "WHERE id=?",
            (freq, n_papers, gloss, phonetic, row["id"]))
        return row["id"]
    cur = conn.execute(
        "INSERT INTO words(account_id,word,tier,freq,n_papers,is_domain,"
        "gloss_zh,phonetic) VALUES(?,?,?,?,?,?,?,?)",
        (account_id, word, tier, freq, n_papers, 1 if is_domain else 0,
         gloss, phonetic))
    return cur.lastrowid


def upsert_context(conn, paper_id: int, word_id: int, sentence: str,
                   section: str) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO contexts(paper_id, word_id, sentence, section) "
        "VALUES(?,?,?,?)",
        (paper_id, word_id, sentence.strip(), section))


def backfill_phonetics(conn: sqlite3.Connection, account_id: int = 0) -> int:
    """Fill empty phonetics from ECDICT then ipa table (whole DB when account=0)."""
    if account_id:
        cur = conn.execute(
            "UPDATE words SET phonetic=COALESCE("
            "(SELECT d.phonetic FROM dict d WHERE d.word=words.word), "
            "(SELECT i.ipa FROM ipa i WHERE i.word=words.word), '') "
            "WHERE account_id=? AND phonetic=''", (account_id,))
    else:
        cur = conn.execute(
            "UPDATE words SET phonetic=COALESCE("
            "(SELECT d.phonetic FROM dict d WHERE d.word=words.word), "
            "(SELECT i.ipa FROM ipa i WHERE i.word=words.word), '') "
            "WHERE phonetic=''")
    conn.commit()
    return cur.rowcount
