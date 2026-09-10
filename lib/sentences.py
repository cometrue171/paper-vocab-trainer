"""Sentence segmentation and finding example sentences that contain a word."""
import re

# sentence boundaries: . ! ? followed by space + capital/quote/number
_SENT_RE = re.compile(r"[.!?](?=\s+(?:[A-Z0-9\"'`(]|$))")
_WS = re.compile(r"\s+")
_WORD = re.compile(r"[A-Za-z]+")


def split_sentences(text: str) -> list[str]:
    """Naive but robust-enough segmentation for harvesting example sentences."""
    if not text:
        return []
    text = _WS.sub(" ", text)
    parts = _SENT_RE.split(text)
    return [p.strip() for p in parts if len(p.strip()) >= 4]


def token_set(sentence: str) -> set[str]:
    return set(t.lower() for t in _WORD.findall(sentence))


def find_contexts(text: str, lemma: str, limit: int = 2,
                  max_len: int = 260) -> list[str]:
    """Whole sentences of text in which `lemma` (any case) appears."""
    found = []
    for s in split_sentences(text):
        if lemma.lower() in token_set(s):
            s = _WS.sub(" ", s).strip()
            if 8 <= len(s) <= max_len:
                found.append(s)
            if len(found) >= limit:
                break
    return found
