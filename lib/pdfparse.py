"""PDF full-text extraction & denoising (pypdf)."""
import re
from pathlib import Path

from pypdf import PdfReader

from .paths import FULLTEXT

# lines that usually follow the real body and only add vocabulary noise
_REF_HEADERS = re.compile(
    r"^\s*(references|bibliography|literature\s+cited|acknowledgements?|"
    r"supporting\s+information|appendix)\s*$", re.IGNORECASE)


def pdf_to_text(pdf_path: str | Path) -> str:
    """Extract all text pages; return '' if unreadable."""
    try:
        reader = PdfReader(str(pdf_path))
        pages = []
        for p in reader.pages:
            try:
                pages.append(p.extract_text() or "")
            except Exception:
                continue
        return "\n".join(pages)
    except Exception:
        return ""


def clean_text(raw: str) -> str:
    """Normalise whitespace and cut trailing reference/back-matter noise."""
    raw = raw.replace("\x00", "")
    lines = raw.splitlines()
    body = []
    cut = False
    for ln in lines:
        if _REF_HEADERS.match(ln.strip()):
            cut = True
        if not cut:
            body.append(ln)
    txt = "\n".join(body)
    txt = re.sub(r"\s+", " ", txt)
    return txt.strip()


def save_fulltext(paper_id: int, slug: str, text: str) -> str | None:
    """Write cleaned text under fulltext/<slug>.txt; return filename or None."""
    if not text or len(text) < 400:
        return None
    FULLTEXT.mkdir(parents=True, exist_ok=True)
    name = f"{paper_id}-{slug}.txt"
    (FULLTEXT / name).write_text(text, encoding="utf-8")
    return name


def load_fulltext(name: str) -> str:
    p = FULLTEXT / name
    return p.read_text(encoding="utf-8") if p.exists() else ""
