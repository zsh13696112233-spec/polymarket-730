from __future__ import annotations

import json
from collections.abc import Collection
from typing import Any, Literal

WHALE_STATISTICS_CATEGORIES = (
    ("esports", "电竞"),
    ("sports", "传统体育"),
    ("politics", "政治"),
    ("crypto", "加密"),
    ("science_tech", "科学与科技"),
    ("entertainment", "娱乐"),
    ("other", "其他"),
)
WHALE_STATISTICS_CATEGORY_LABELS = dict(WHALE_STATISTICS_CATEGORIES)
WHALE_STATISTICS_SCIENCE_TECH_TAGS = frozenset(
    {
        "science",
        "spacex",
        "space-exploration",
        "software-updates",
        "climate",
        "weather",
    }
)
WHALE_STATISTICS_ENTERTAINMENT_TAGS = frozenset(
    {
        "movies",
        "music",
        "awards",
        "box-office",
        "celebrity-events",
        "new-releases",
        "gaming",
        "video-games",
    }
)
WHALE_STATISTICS_SUBCATEGORIES = {
    "esports": (
        ("dota-2", "Dota 2", frozenset({"dota-2"})),
        ("counter-strike-2", "CS2", frozenset({"counter-strike-2"})),
        ("league-of-legends", "英雄联盟", frozenset({"league-of-legends"})),
        ("other-esports", "其他电竞", frozenset()),
    ),
    "sports": (
        ("soccer", "足球", frozenset({"soccer"})),
        ("tennis", "网球", frozenset({"tennis"})),
        ("basketball", "篮球", frozenset({"basketball"})),
        ("baseball", "棒球", frozenset({"baseball"})),
        ("football", "美式橄榄球", frozenset({"football"})),
        ("hockey", "冰球", frozenset({"hockey"})),
        ("mma", "综合格斗", frozenset({"mma"})),
        ("boxing", "拳击", frozenset({"boxing"})),
        ("cricket", "板球", frozenset({"cricket"})),
        ("golf", "高尔夫", frozenset({"golf"})),
        ("motorsports", "赛车", frozenset({"formula-1", "f1"})),
        ("other-sports", "其他体育", frozenset()),
    ),
}


def _json_list(value: str | list[Any] | tuple[Any, ...] | None) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def classify_market(
    tags: str | list[Any] | tuple[Any, ...] | None,
) -> dict[str, str | None]:
    slugs = {
        str(tag.get("slug") or "").strip().lower()
        for tag in _json_list(tags)
        if isinstance(tag, dict) and tag.get("slug")
    }
    if "esports" in slugs:
        category = "esports"
    elif "sports" in slugs:
        category = "sports"
    elif "politics" in slugs:
        category = "politics"
    elif "crypto" in slugs:
        category = "crypto"
    elif slugs & WHALE_STATISTICS_SCIENCE_TECH_TAGS:
        category = "science_tech"
    elif slugs & WHALE_STATISTICS_ENTERTAINMENT_TAGS:
        category = "entertainment"
    else:
        category = "other"

    subcategory: str | None = None
    subcategory_label: str | None = None
    definitions = WHALE_STATISTICS_SUBCATEGORIES.get(category, ())
    for key, label, matching_slugs in definitions:
        if matching_slugs and slugs & matching_slugs:
            subcategory = key
            subcategory_label = label
            break
    if definitions and subcategory is None:
        subcategory, subcategory_label, _ = definitions[-1]
    return {
        "category": category,
        "category_label": WHALE_STATISTICS_CATEGORY_LABELS[category],
        "subcategory": subcategory,
        "subcategory_label": subcategory_label,
    }


WhaleMarketCategory = Literal[
    "esports",
    "sports",
    "politics",
    "crypto",
    "science_tech",
    "entertainment",
    "other",
]


WhaleCategorySelection = (
    WhaleMarketCategory
    | Literal[
        "dota-2",
        "counter-strike-2",
        "league-of-legends",
        "other-esports",
        "soccer",
        "tennis",
        "basketball",
        "baseball",
        "football",
        "hockey",
        "mma",
        "boxing",
        "cricket",
        "golf",
        "motorsports",
        "other-sports",
    ]
)


def normalize_category_selection(values: list[str]) -> list[str]:
    parents = {
        key: parent
        for parent, children in WHALE_STATISTICS_SUBCATEGORIES.items()
        for key, _, _ in children
    }
    return [key for key in dict.fromkeys(values) if parents.get(key) not in values]


def matches_categories(classification: dict[str, str | None], selected: Collection[str]) -> bool:
    return classification["category"] in selected or (
        classification.get("subcategory") is not None and classification["subcategory"] in selected
    )
