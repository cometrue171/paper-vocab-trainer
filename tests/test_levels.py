"""Difficulty-floor tiers: each level must skip exactly its own exam bands."""
from lib import gloss
from lib.tokenize import tier_for

LEVELS = ["zk", "cet4", "cet6", "ky"]


def tier(tags, is_domain=False, level="zk"):
    return tier_for("w", set(tags.split()), is_domain, gloss.extra_tags(level))


def test_entry_level_only_skips_junior_high_words():
    assert tier("zk") is None              # 入门档：只跳过中考基础词
    assert tier("gk") == "D1"              # 高考/四级词也学
    assert tier("cet4") == "D1"
    assert tier("cet6") == "D1"


def test_standard_level_skips_through_cet4():
    assert tier("gk", level="cet4") is None
    assert tier("cet4", level="cet4") is None
    assert tier("cet6", level="cet4") == "D1"


def test_higher_levels_skip_more():
    assert tier("cet6", level="cet6") is None
    assert tier("ky", level="cet6") == "D1"
    assert tier("ky", level="ky") is None
    assert tier("toefl", level="ky") == "D1"


def test_domain_words_and_untagged_words():
    assert tier("", is_domain=True, level="cet6") == "D0"        # 领域词
    assert tier("", level="cet6") == "D2"                        # 无标签 → 语境词
    assert tier("cet6", is_domain=True, level="cet6") is None    # 低于档位的领域词也跳过
