"""Project path helpers — all paths resolve from the project root."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# 允许用环境变量把数据目录指到别处（测试隔离 / 多实例部署）
DATA = Path(os.environ.get("SCIENCE_ENGLISH_DATA") or (ROOT / "data"))
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
