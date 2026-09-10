"""Project path helpers — all paths resolve from the project root."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
WORDLISTS = DATA / "wordlists"
FULLTEXT = ROOT / "fulltext"
INBOX = ROOT / "papers" / "inbox"
STATIC = ROOT / "static"
DB_PATH = DATA / "lit.db"
ECDICT_CSV = DATA / "ecdict.csv"
SEED_DOMAIN = DATA / "seed_domain.tsv"
BASE_WORDS_TXT = WORDLISTS / "base_words.txt"


def ensure_dirs() -> None:
    for d in (DATA, WORDLISTS, FULLTEXT, INBOX, STATIC):
        d.mkdir(parents=True, exist_ok=True)
