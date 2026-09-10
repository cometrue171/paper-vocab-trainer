"""Offline English->Chinese dictionary (ECDICT) import, lookup and word tiering.

ECDICT is a free English-Chinese dictionary (Apache-2.0 licensed data) published at
https://github.com/skywind3000/ECDICT . Each row's `tag` column marks which exam band a
word belongs to (zk=gk=cet4=...). We use those tags both to give Chinese glosses and to
classify words into tiers:
  base  ->  zk/gk/cet4 words, assumed already known, skipped from the learning pool
  D1    ->  academic/exam words (cet6/ky/toefl/ielts/gre/...) still worth learning
  D2    ->  no exam tag / unknown word found in corpus
"""
import csv
import io
import re
import sqlite3
import sys

import requests

from .paths import ECDICT_CSV

ECDICT_URL = "https://raw.githubusercontent.com/skywind3000/ECDICT/master/ecdict.csv"

BASE_TAGS = {"zk", "gk", "cet4"}                 # assumed known: skip unless domain
LEARN_TAGS = {"cet6", "ky", "toefl", "ielts", "gre", "sat", "gmat"}

# 账号“目标难度档”额外视为已知/跳过的标签（min_level 设置）
LEVEL_EXTRA = {
    "zk": {"zk"},                                   # 入门：只跳过中考最基础词
    "cet4": {"zk", "gk", "cet4"},                   # 标准：高考~四级及以下跳过
    "cet6": {"zk", "gk", "cet4", "cet6"},           # 提升：六级及以下视为已知
    "ky": {"zk", "gk", "cet4", "cet6", "ky"},       # 高阶：考研及以下视为已知
}


def extra_tags(level: str) -> set[str]:
    return set(LEVEL_EXTRA.get(level or "cet4", set()))

# glue/function tokens that have no dict entry but must never enter the pool
GLUE_WORDS = {
    "the", "of", "and", "to", "in", "for", "is", "are", "was", "were", "be", "been",
    "being", "with", "on", "at", "by", "from", "as", "or", "an", "a", "not", "than",
    "that", "this", "these", "those", "it", "its", "they", "them", "their", "there",
    "we", "our", "you", "your", "he", "she", "his", "her", "which", "who", "whom",
    "whose", "when", "where", "why", "how", "what", "if", "then", "than", "but",
    "so", "also", "both", "each", "such", "more", "most", "some", "any", "no",
    "all", "can", "could", "may", "might", "shall", "should", "will", "would",
    "must", "do", "does", "did", "have", "has", "had", "has", "into", "onto", "upon",
    "etc", "eg", "ie", "vs", "via", "per", "et", "al", "pp", "fig", "vol", "no",
}

CSV_FIELDS = ["word", "phonetic", "definition", "translation", "pos", "collins",
              "oxford", "tag", "bnc", "frq", "exchange", "detail", "audio"]


def has_ecdict_file() -> bool:
    return ECDICT_CSV.exists() and ECDICT_CSV.stat().st_size > 10_000


def download_ecdict(progress: bool = True) -> str:
    """Download the ECDICT csv once, cached at data/ecdict.csv."""
    if has_ecdict_file():
        return str(ECDICT_CSV)
    ECDICT_CSV.parent.mkdir(parents=True, exist_ok=True)
    r = requests.get(ECDICT_URL, stream=True, timeout=120)
    r.raise_for_status()
    total = int(r.headers.get("content-length") or 0)
    done = 0
    with open(ECDICT_CSV, "wb") as f:
        for chunk in r.iter_content(chunk_size=1 << 20):
            f.write(chunk)
            done += len(chunk)
            if progress and total:
                sys.stdout.write(f"\r下载词典 {done / 1e6:.1f}/{total / 1e6:.1f} MB")
                sys.stdout.flush()
    if progress:
        sys.stdout.write("\n")
    return str(ECDICT_CSV)


def import_ecdict(conn: sqlite3.Connection, csv_path: str | None = None) -> int:
    """Stream ecdict.csv into the `dict` table. Returns number of rows inserted."""
    path = csv_path or download_ecdict()
    conn.execute("DELETE FROM dict")
    conn.commit()
    n = 0
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.reader(f, delimiter=",")
        header, firstrow = None, None
        for first in reader:
            if first and first[0].strip().lower() == "word":
                header = [c.strip().lower() for c in first]
                break
            header, firstrow = CSV_FIELDS, first   # 无表头：按标准顺序
            break

        def as_row(values):
            return dict(zip(header, values))

        batch = []
        rows_iter = ([as_row(firstrow)] if firstrow else []) + [as_row(v) for v in reader]
        for row in rows_iter:
            w = (row.get("word") or "").strip().lower()
            if not w or len(w) > 60:
                continue
            batch.append((w, (row.get("phonetic") or "")[:60],
                          (row.get("translation") or "")[:2000],
                          (row.get("tag") or "")[:120],
                          (row.get("pos") or "")[:60],
                          re.sub(r"\s+", " ", (row.get("definition") or ""))[:600]))
            if len(batch) >= 5000:
                conn.executemany(
                    "INSERT OR REPLACE INTO dict(word,phonetic,translation_zh,tag,pos,definition) "
                    "VALUES(?,?,?,?,?,?)", batch)
                batch = []
                n += 5000
        if batch:
            conn.executemany(
                "INSERT OR REPLACE INTO dict(word,phonetic,translation_zh,tag,pos,definition) "
                "VALUES(?,?,?,?,?,?)", batch)
            n += len(batch)
    conn.commit()
    return n


def lookup(conn: sqlite3.Connection, word: str):
    return conn.execute(
        "SELECT word,phonetic,translation_zh,tag FROM dict WHERE word=?",
        (word.lower(),),
    ).fetchone()


def gloss(conn: sqlite3.Connection, word: str) -> str:
    """Short Chinese gloss: translation_zh, cleaned of markup, truncated."""
    row = lookup(conn, word)
    if not row:
        return ""
    t = row["translation_zh"] or ""
    t = re.sub(r"<[^>]+>", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    # translation often lists multiple senses "…;…"; keep a compact slice
    return t[:300]


def download_ipa(progress: bool = True) -> str:
    """Download the open ipa-dict en_US word->IPA table (cache at data/)."""
    from .paths import DATA
    target = DATA / "ipa_en_US.txt"
    if target.exists() and target.stat().st_size > 100_000:
        return str(target)
    url = "https://raw.githubusercontent.com/open-dict-data/ipa-dict/master/data/en_US.txt"
    r = requests.get(url, timeout=120)
    r.raise_for_status()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(r.content)
    return str(target)


def import_ipa(conn: sqlite3.Connection) -> int:
    """Load en_US word->IPA into the `ipa` table. Returns rows imported."""
    from .paths import DATA
    path = download_ipa()
    conn.execute("DELETE FROM ipa")
    conn.commit()
    n = 0
    batch = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "\t" not in line:
                continue
            w, p = line.rstrip("\n").split("\t", 1)
            w = w.strip().lower()
            p = p.strip()
            if w and p:
                batch.append((w, p))
            if len(batch) >= 5000:
                conn.executemany(
                    "INSERT OR IGNORE INTO ipa(word,ipa) VALUES(?,?)", batch)
                batch = []
                n += 5000
    if batch:
        conn.executemany("INSERT OR IGNORE INTO ipa(word,ipa) VALUES(?,?)", batch)
        n += len(batch)
    conn.commit()
    return n


_POS_CN = {"n": "n.", "v": "v.", "vt": "vt.", "vi": "vi.", "adj": "adj.", "adv": "adv.",
           "prep": "prep.", "pre": "prep.", "conj": "conj.", "pron": "pron.",
           "num": "num.", "art": "art.", "int": "int.", "aux": "aux.",
           "abbr": "abbr.", "pl": "pl.", "j": "adj."}


def pos_of(conn: sqlite3.Connection, word: str) -> str:
    """词性：优先 ECDICT 的 pos 字段（如 'n:64/v:36'），否则解析释义文本前缀。"""
    row = conn.execute("SELECT pos, translation_zh, definition FROM dict WHERE word=?",
                       (word.lower(),)).fetchone()
    if not row:
        return ""
    if row["pos"]:
        items = []
        for chunk in re.split(r"[/,;|]", row["pos"]):
            code, _, pct = chunk.partition(":")
            try:
                w = float(pct or 0)
            except ValueError:
                w = 0.0
            if code.strip():
                items.append((w, code.strip().lower()))
        items.sort(reverse=True)
        out = [_POS_CN[c] for _, c in items[:2] if c in _POS_CN]
        if out:
            return " ".join(dict.fromkeys(out))
    # 文本解析：中文释义、英文释义里出现的 n. / v. / adj. …
    text = (row["translation_zh"] or "") + " " + (row["definition"] or "")
    pat = re.compile(r"(?<![a-zA-Z])(n|v|vt|vi|adj|adv|prep|pre|conj|pron|num|art|int|aux|abbr|pl)\.",
                     re.I)
    out = []
    for m in pat.finditer(text):
        label = _POS_CN.get(m.group(1).lower())
        if label and label not in out:
            out.append(label)
        if len(out) >= 2:
            break
    return " ".join(out)


def tag_set(row) -> set[str]:
    return set((row["tag"] or "").split()) if row else set()


def classify(word: str, row) -> str:
    """Return 'base' (skip), 'D0' (domain handled elsewhere), 'D1' or 'D2'."""
    tags = tag_set(row)
    if tags & BASE_TAGS:
        return "base"
    if tags & LEARN_TAGS:
        return "D1"
    return "D2"
