"""Orchestrate turning raw paper text into pool words, scoped to one account."""
import hashlib
import pathlib
import sqlite3
import sys

import requests

from . import corpus, directions, gloss, pdfparse, tokenize
from .db import backfill_phonetics, get_setting, user_row
from .paths import FULLTEXT, INBOX


def account_seed(conn: sqlite3.Connection, account_id: int):
    u = user_row(conn, uid=account_id)
    return directions.seed_file(u["direction"] if u else "")


def account_skip_tags(conn: sqlite3.Connection, account_id: int) -> set[str]:
    """Difficulty-floor tags this account treats as already known."""
    return gloss.extra_tags(get_setting(conn, account_id, "min_level", "cet4"))


def _paper_sources(conn: sqlite3.Connection, pid: int):
    row = conn.execute("SELECT * FROM papers WHERE id=?", (pid,)).fetchone()
    if not row:
        return "", ""
    body = pdfparse.load_fulltext(row["fulltext_file"]) if row["has_fulltext"] else ""
    return (row["abstract"] or ""), body


def extract_pending(conn: sqlite3.Connection, account_id: int,
                    limit: int = 0, verbose: bool = True):
    """Harvest words/contexts for every unprocessed paper of this account."""
    q = "SELECT id FROM papers WHERE extracted=0 AND account_id=? ORDER BY id"
    ids = [r["id"] for r in conn.execute(q, (account_id,)).fetchall()]
    if limit:
        ids = ids[:limit]
    mapper, domain_gloss = tokenize.prepare(conn, account_seed(conn, account_id))
    skip_tags = account_skip_tags(conn, account_id)
    total_words = 0
    for pid in ids:
        abstract, body = _paper_sources(conn, pid)
        if not (abstract or body):
            conn.execute("UPDATE papers SET extracted=1 WHERE id=?", (pid,))
            continue
        try:
            res = tokenize.harvest_paper(
                conn, account_id, pid, abstract, body,
                mapper=mapper, domain_gloss=domain_gloss, skip_tags=skip_tags)
            conn.execute("UPDATE papers SET extracted=1 WHERE id=?", (pid,))
            conn.commit()
            total_words += res["words_added"]
            if verbose:
                print(f"  paper#{pid}: +{res['words_added']} words")
        except Exception as e:  # noqa: BLE001
            print(f"  paper#{pid}: SKIP ({e})", file=sys.stderr)
            conn.rollback()
    n = backfill_phonetics(conn, account_id)
    if verbose:
        print(f"extract done, total new word-rows added: {total_words}"
              + (f", backfilled phonetic {n}" if n else ""))
    return total_words


def ingest_local_pdf(conn: sqlite3.Connection, account_id: int, pdf_path: str,
                     verbose: bool = True) -> int:
    """Parse a user PDF into this account's paper list + pool."""
    path = pathlib.Path(pdf_path)
    if not path.exists():
        print(f"not found: {pdf_path}", file=sys.stderr)
        return 0
    raw = pdfparse.pdf_to_text(path)
    text = pdfparse.clean_text(raw)
    if len(text) < 300:
        print(f"  {path.name}: no readable text (scanned PDF?)", file=sys.stderr)
        return 0

    title = tokenize.sentences.split_sentences(text)[:1][0][:400] if text else path.stem
    doi = "local:" + hashlib.md5(str(path.resolve()).encode()).hexdigest()[:12]
    existing = conn.execute(
        "SELECT id FROM papers WHERE account_id=? AND doi=?",
        (account_id, doi)).fetchone()
    if existing:
        print(f"  {path.name}: already imported (paper#{existing['id']}), skip")
        return 0
    cur = conn.execute(
        "INSERT INTO papers(account_id,doi,title,abstract,year,source,url,oa,"
        "has_fulltext,fulltext_file,extracted,is_reading) "
        "VALUES(?,?,?,?,?,?,?,0,0,'',0,0)",
        (account_id, doi, title, "", None, "pdf", str(path)))
    pid = cur.lastrowid
    slug = corpus.safe_slug(path.stem)
    name = pdfparse.save_fulltext(pid, slug, text)
    if name:
        conn.execute("UPDATE papers SET has_fulltext=1, fulltext_file=? WHERE id=?",
                     (name, pid))
        conn.commit()
        mapper, domain_gloss = tokenize.prepare(conn, account_seed(conn, account_id))
        skip_tags = account_skip_tags(conn, account_id)
        res = tokenize.harvest_paper(conn, account_id, pid, "", text,
                                     mapper=mapper, domain_gloss=domain_gloss,
                                     skip_tags=skip_tags)
        conn.execute("UPDATE papers SET extracted=1 WHERE id=?", (pid,))
        conn.commit()
        if verbose:
            print(f"  imported {path.name} -> paper#{pid}, +{res['words_added']} words")
        return res["words_added"]
    conn.rollback()
    return 0


def download_oa_fulltext(conn: sqlite3.Connection, account_id: int,
                         limit: int = 40, verbose: bool = True) -> int:
    """Download & parse open-access full texts for one account's papers."""
    got = 0
    session = requests.Session()
    for pid, doi, url in corpus.oa_pdf_urls(conn, account_id, limit):
        if got >= limit:
            break
        slug = corpus.safe_slug(doi)
        try:
            r = session.get(url, timeout=60)
            r.raise_for_status()
            if len(r.content) < 10_000 or r.content[:5] != b"%PDF-":
                continue
        except Exception as e:  # noqa: BLE001
            if verbose:
                print(f"  skip download {doi}: {e}")
            continue
        pdf_path = FULLTEXT / f"{pid}-{slug}.pdf"
        pdf_path.write_bytes(r.content)
        raw = pdfparse.pdf_to_text(pdf_path)
        text = pdfparse.clean_text(raw)
        name = pdfparse.save_fulltext(pid, slug, text)
        conn.execute(
            "UPDATE papers SET has_fulltext=1, fulltext_file=?, oa_pdf='' WHERE id=?",
            (name or "", pid))
        conn.commit()
        got += 1
        if verbose:
            print(f"  OA fulltext #{pid}: {len(text)} chars")
    if verbose:
        print(f"downloaded {got} OA full texts")
    return got


def delete_paper(conn: sqlite3.Connection, account_id: int, paper_id: int) -> bool:
    """Delete one of THIS account's papers and its descendants/files."""
    row = conn.execute(
        "SELECT doi, fulltext_file, url FROM papers "
        "WHERE id=? AND account_id=?", (paper_id, account_id)).fetchone()
    if not row:
        return False
    conn.execute(
        "DELETE FROM sent_notes WHERE context_id IN "
        "(SELECT id FROM contexts WHERE paper_id=?)", (paper_id,))
    conn.execute("DELETE FROM contexts WHERE paper_id=?", (paper_id,))
    conn.execute("DELETE FROM papers WHERE id=?", (paper_id,))
    if row["fulltext_file"]:
        f = FULLTEXT / row["fulltext_file"]
        if f.exists():
            f.unlink()
    if str(row["doi"] or "").startswith("local:"):
        p = pathlib.Path(row["url"] or "")
        if p.exists() and INBOX in p.parents:
            p.unlink()
    conn.commit()
    return True
