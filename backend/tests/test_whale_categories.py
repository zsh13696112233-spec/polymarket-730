import pytest

from backend.whale_categories import (
    WHALE_STATISTICS_SUBCATEGORIES,
    classify_market,
    matches_categories,
)


@pytest.mark.parametrize(
    ("parent", "key", "slugs"),
    [
        (parent, key, slugs)
        for parent, children in WHALE_STATISTICS_SUBCATEGORIES.items()
        for key, _, slugs in children
    ],
)
def test_official_subcategories_and_parent_selection(parent, key, slugs):
    tags = [{"slug": slug} for slug in [parent, *slugs]]
    classification = classify_market(tags)
    assert classification["subcategory"] == key
    assert matches_categories(classification, [key])
    assert matches_categories(classification, [parent])
    assert not matches_categories(classification, ["politics"])


def test_esports_takes_precedence_and_unknown_tags_do_not_guess():
    classification = classify_market(
        [{"slug": "sports"}, {"slug": "esports"}, {"slug": "counter-strike-2"}]
    )
    assert classification["category"] == "esports"
    assert matches_categories(classification, ["counter-strike-2"])
    assert not matches_categories(classification, ["sports", "dota-2"])
    assert (
        classify_market([{"slug": "sports"}, {"slug": "unknown"}])["subcategory"] == "other-sports"
    )
    assert (
        classify_market([{"slug": "esports"}, {"slug": "unknown"}])["subcategory"]
        == "other-esports"
    )
    assert classify_market(None)["category"] == "other"
