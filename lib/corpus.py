"""Harvest literature metadata + abstracts from the OpenAlex API (no key needed)."""
import re
import sqlite3
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

API = "https://api.openalex.org/works"
MAILTO = "suwiky.local@example.com"          # polite API usage
PER_PAGE = 50

# 常用期刊名 -> ISSN（OpenAlex 用 ISSN 过滤来源）
JOURNAL_ISSN = {
    "nature food": "2662-1355",
    "global food security": "2211-9124",
    "food policy": "0306-9192",
    "agricultural systems": "0308-521X",
    # 英语教学 / 二语研究（teacher 账号用）
    "tesol quarterly": "0039-8322",
    "applied linguistics": "0142-6001",
    "language teaching": "0261-4448",
    "language learning": "0023-8333",
    "second language research": "0267-6583",
}


def source_issn(source: str) -> str | None:
    s = (source or "").strip()
    if not s:
        return None
    if len(s) == 9 and s[4] == "-":
        return s                       # 已是 ISSN 形式
    return JOURNAL_ISSN.get(s.lower())


def make_session() -> requests.Session:
    """Session that tolerates the flaky local proxy / transient 5xx."""
    s = requests.Session()
    retry = Retry(total=4, backoff_factor=1.2,
                  status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET"]))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    return s


def clean_doi(doi: str) -> str:
    if not doi:
        return ""
    return doi.lower().replace("https://doi.org/", "").replace("http://doi.org/", "").strip()


def reconstruct_abstract(inv: dict | None) -> str:
    """OpenAlex abstracts arrive as an inverted index; rebuild plain text."""
    if not inv:
        return ""
    pos = {}
    for w, idxs in inv.items():
        for i in idxs:
            pos[i] = w
    if not pos:
        return ""
    return " ".join(pos[i] for i in sorted(pos))


def search(term: str, pages: int = 1, session: requests.Session | None = None,
           source: str | None = None) -> list[dict]:
    """Query OpenAlex. `term` matches title+abstract; `source` restricts the
    journal/source (e.g. "Nature Food"). Either may be empty (source-only)."""
    s = session or requests.Session()
    out = []
    filters = []
    if term:
        filters.append(f'title_and_abstract.search:"{term}"')
    if source:
        issn = source_issn(source)
        if not issn:
            raise ValueError(f"未识别期刊来源: {source}（请传 ISSN 或加入 JOURNAL_ISSN 映射）")
        filters.append(f"primary_location.source.issn:{issn}")
    filters.append("language:en")
    filters.append("type:article")
    sort = "publication_date:desc" if not term else "relevance_score:desc"
    params = {
        "filter": ",".join(filters),
        "sort": sort,
        "per-page": PER_PAGE,
        "mailto": MAILTO,
    }
    for page in range(1, pages + 1):
        params["page"] = page
        r = s.get(API, params=params, timeout=40)
        r.raise_for_status()
        data = r.json()
        for w in data.get("results", []):
            rec = {
                "doi": clean_doi(w.get("doi") or ""),
                "title": (w.get("title") or "").strip(),
                "abstract": reconstruct_abstract(w.get("abstract_inverted_index")),
                "year": w.get("publication_year"),
                "source": ((w.get("primary_location") or {}).get("source") or {}).get(
                    "display_name", "") or "",
                "url": w.get("doi") or w.get("landing_page_url") or "",
                "oa": 1 if (w.get("open_access") or {}).get("is_oa") else 0,
                "pdf_url": ((w.get("best_oa_location") or {}).get("pdf_url") or ""),
            }
            if rec["title"]:
                out.append(rec)
        time.sleep(0.35)
    return out


def fetch_into_db(conn: sqlite3.Connection, terms: list[str], account_id: int,
                  target_new: int = 300, source: str | None = None) -> int:
    """Insert new papers for one account until ~target_new fresh works stored."""
    session = make_session()
    added = 0
    seen = set(r["doi"] for r in conn.execute(
        "SELECT doi FROM papers WHERE account_id=? AND doi != ''",
        (account_id,)).fetchall())
    for term in terms:
        if added >= target_new:
            break
        need = target_new - added
        pages = max(1, (need + PER_PAGE - 1) // PER_PAGE)
        try:
            results = search(term, pages=pages, session=session, source=source)
        except (requests.RequestException, ValueError) as e:
            print(f"  [warn] 检索词「{term}」失败: {e}")
            continue
        for rec in results:
            if added >= target_new:
                break
            if not rec["doi"] or rec["doi"] in seen:
                continue
            seen.add(rec["doi"])
            conn.execute(
                "INSERT OR IGNORE INTO papers(account_id,doi,title,abstract,year,"
                "source,url,oa,has_fulltext,fulltext_file,oa_pdf,extracted,is_reading) "
                "VALUES(?,?,?,?,?,?,?,?,0,'',?,0,0)",
                (account_id, rec["doi"], rec["title"], rec["abstract"],
                 rec["year"], rec["source"], rec["url"], rec["oa"],
                 rec["pdf_url"]),
            )
            added += 1
    conn.commit()
    return added


def oa_pdf_urls(conn: sqlite3.Connection, account_id: int,
                limit: int) -> list[tuple[int, str, str]]:
    """Papers of one account with an OA pdf url we haven't parsed yet."""
    rows = conn.execute(
        "SELECT p.id, p.doi, p.oa_pdf FROM papers p "
        "WHERE p.account_id=? AND p.oa=1 AND p.oa_pdf!='' AND p.extracted=0 "
        "ORDER BY p.year DESC LIMIT ?",
        (account_id, limit),
    ).fetchall()
    return [(r["id"], r["doi"], r["oa_pdf"]) for r in rows]


def safe_slug(doi: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", doi)[:100] or "paper"
