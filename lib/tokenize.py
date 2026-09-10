"""Tokenise corpus text, fold plurals, classify into tiers and harvest to a pool.

Everything harvested is scoped to one account (account_id); which domain seed
file defines the D0 words depends on the account direction.
"""
import re
import sqlite3
from collections import Counter
from pathlib import Path

from . import gloss as gloss_mod
from . import sentences
from .db import upsert_context, upsert_word
from .paths import SEED_DOMAIN

_WORD = re.compile(r"[A-Za-z][A-Za-z']*")


def load_domain_seed(path: str | Path | None = None) -> dict[str, str]:
    """{word: zh_gloss} from a domain-seed TSV (single-word entries only)."""
    out: dict[str, str] = {}
    p = Path(path) if path else Path(SEED_DOMAIN)
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "\t" in line:
            w, zh = line.split("\t", 1)
        else:
            w, zh = line, ""
        w = w.strip().lower()
        if re.fullmatch(r"[a-z]+", w):
            out[w] = zh.strip()
    return out


class CanonMapper:
    """Folds plural/verb-suffix tokens to a dictionary headword when safe."""

    def __init__(self, conn: sqlite3.Connection, domain: set[str]):
        self.conn = conn
        self.domain = domain
        # preload dictionary headwords once: membership is the hot path
        self.known: set[str] = {
            r[0] for r in conn.execute("SELECT word FROM dict").fetchall()
        }
        self.cache: dict[str, str] = {}

    def _known(self, w: str) -> bool:
        return w in self.known

    def canon(self, w: str) -> str:
        w = w.lower()
        if w in self.cache:
            return self.cache[w]
        orig = w
        if w in self.domain or len(w) <= 2 or w.endswith("'s"):
            res = w
        elif self._known(w):
            res = w
        else:
            cand = self._fold(w)
            res = cand if (cand != w and self._known(cand)) else w
        self.cache[orig] = res
        return res

    @staticmethod
    def _fold(w: str) -> str:
        if w.endswith("ies") and len(w) > 4:
            return w[:-3] + "y"
        if w.endswith("es") and len(w) > 4 and not w.endswith(("sses", "xses")):
            stem = w[:-2]
            if len(stem) >= 3:
                return stem
        if w.endswith("s") and not w.endswith(("ss", "us", "is", "os", "ous", "sis")):
            return w[:-1]
        return w


def prepare(conn: sqlite3.Connection, seed_file: str | Path | None = None):
    """Build (mapper, domain_gloss) once per account & direction."""
    gloss = load_domain_seed(seed_file)
    return CanonMapper(conn, set(gloss)), gloss


def harvest_paper(conn: sqlite3.Connection, account_id: int, paper_id: int,
                  abstract: str, body_text: str = "",
                  mapper: CanonMapper | None = None,
                  domain_gloss: dict | None = None,
                  skip_tags: set[str] | None = None) -> dict:
    """Tokenise one paper into the account's word pool.

    Pass a shared (mapper, domain_gloss) from prepare() when harvesting many
    papers. `skip_tags` are ECDICT exam bands treated as already-known for this
    account (its target difficulty floor). Returns {'words_added', 'tokens'}.
    """
    if mapper is None or domain_gloss is None:
        mapper, domain_gloss = prepare(conn)
    domain = mapper.domain
    skip_tags = skip_tags or set()

    total_occ: Counter = Counter()
    contexts: dict[str, list[tuple[str, str]]] = {}

    for section, text in (("abstract", abstract), ("body", body_text)):
        if not text:
            continue
        for sent in sentences.split_sentences(text):
            lemmas = [mapper.canon(t) for t in _WORD.findall(sent)]
            if not lemmas:
                continue
            for lem in lemmas:
                total_occ[lem] += 1
            for lem in dict(Counter(lemmas)):
                lst = contexts.setdefault(lem, [])
                if len(lst) < 4:
                    lst.append((sent, section))

    added = 0
    for lem, occ in total_occ.items():
        if lem in gloss_mod.GLUE_WORDS or len(lem) < 3:
            continue
        row = gloss_mod.lookup(conn, lem)
        # 目标难度档：低于该档（含领域基础词）视为已知，不收集
        if row and (gloss_mod.tag_set(row) & skip_tags):
            continue
        is_domain = lem in domain
        cls = "D0" if is_domain else gloss_mod.classify(lem, row)
        if cls == "base":
            continue
        if cls == "D2" and occ < 2:
            continue
        gloss_zh = domain_gloss.get(lem, "") or gloss_mod.gloss(conn, lem)
        phon = (row["phonetic"] if row and row["phonetic"] else "")[:60]
        wid = upsert_word(conn, account_id, lem, tier=cls, freq=occ,
                          n_papers=1, is_domain=is_domain, gloss=gloss_zh,
                          phonetic=phon)
        added += 1
        cands = sorted(contexts.get(lem, []),
                       key=lambda c: (0 if c[1] == "abstract" else 1, len(c[0])))
        for sent, section in cands[:2]:
            upsert_context(conn, paper_id, wid, sent, section)

    return {"words_added": added, "tokens": int(total_occ.total())}
