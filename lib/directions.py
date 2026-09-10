"""研究方向 -> 领域种子词表 / 默认抓取关键词 的映射。

每个账号有自己的 direction（见 users.direction）。领域核心词(D0)和默认文献抓取
主题都随方向走，从而不同方向的账号建出各自独立的词库。
"""
from .paths import SEED_DOMAIN
from .paths import DATA

SEED_ENGLISH = DATA / "seed_english.tsv"

ENG_MARKERS = ("英语", "english", "英语教学")
PHOS_MARKERS = ("磷", "磷足迹", "环境", "全生命周期")

ENGLISH_DEFAULT_TERMS = (
    "English language teaching|second language acquisition|vocabulary learning|"
    "EFL classroom pedagogy|TESOL teaching methods|language assessment reading "
    "comprehension|teacher education ELT"
)


def seed_file(direction: str):
    """Pick the domain-seed TSV for an account direction."""
    d = (direction or "").lower()
    if "英语" in direction or "english" in d or "英语教学" in direction:
        return SEED_ENGLISH
    return SEED_DOMAIN


def default_terms(direction: str) -> str:
    d = (direction or "").lower()
    if "英语" in direction or "english" in d or "英语教学" in direction:
        return ENGLISH_DEFAULT_TERMS
    return ("global phosphorus footprint|phosphorus life cycle assessment|"
            "anthropogenic phosphorus flows|global phosphorus cycle|"
            "phosphorus use efficiency|phosphorus recovery|eutrophication|"
            "phosphate rock")


def is_english(direction: str) -> bool:
    return seed_file(direction) == SEED_ENGLISH
