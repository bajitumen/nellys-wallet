"""Money-classification predicates and helpers.

These functions decide whether a transaction lands on the spending side, the
income side, or neither. A miscategorization here silently corrupts every
spending / budget / cashflow / income number, so the predicates have their
own dedicated test surface even though they're trivial.
"""

import pytest

import pfc


# ---------------------------------------------------------------------------
# side predicates
# ---------------------------------------------------------------------------

def test_is_income_category_covers_income_and_transfer_in():
    """Broad income-side classification — used for the income/spending split."""
    assert pfc.is_income_category("INCOME") is True
    assert pfc.is_income_category("TRANSFER_IN") is True
    assert pfc.is_income_category("FOOD_AND_DRINK") is False
    assert pfc.is_income_category("TRANSFER_OUT") is False
    assert pfc.is_income_category(None) is False
    assert pfc.is_income_category("") is False


def test_is_strict_income_excludes_transfer_in():
    """Strict income (cashflow + income surface): only INCOME counts."""
    assert pfc.is_strict_income("INCOME") is True
    assert pfc.is_strict_income("TRANSFER_IN") is False
    assert pfc.is_strict_income("FOOD_AND_DRINK") is False
    assert pfc.is_strict_income(None) is False


def test_is_spend_category_excludes_income_and_transfer_in():
    """Spend categories: any primary that isn't on the income side."""
    assert pfc.is_spend_category("FOOD_AND_DRINK") is True
    assert pfc.is_spend_category("TRANSFER_OUT") is True
    assert pfc.is_spend_category("INCOME") is False
    assert pfc.is_spend_category("TRANSFER_IN") is False
    assert pfc.is_spend_category(None) is False


def test_strict_income_and_spend_are_mutually_exclusive():
    """No primary should classify as both income and spend simultaneously."""
    for p in pfc.PFC_TAXONOMY.keys():
        assert not (pfc.is_strict_income(p) and pfc.is_spend_category(p)), p


def test_every_taxonomy_primary_lands_on_some_side():
    """Sanity: every PFC primary either flows through income or spend."""
    for p in pfc.PFC_TAXONOMY.keys():
        on_income = pfc.is_income_category(p)
        on_spend = pfc.is_spend_category(p)
        assert on_income or on_spend, f"{p} routes to neither side"


# ---------------------------------------------------------------------------
# detailed code validation + reverse lookup
# ---------------------------------------------------------------------------

def test_is_valid_primary_recognizes_known_primaries():
    for p in pfc.PFC_TAXONOMY.keys():
        assert pfc.is_valid_primary(p) is True
    assert pfc.is_valid_primary("TOTALLY_BOGUS") is False
    assert pfc.is_valid_primary("") is False


def test_is_valid_detailed_recognizes_known_detailed_codes():
    # Spot-check from each major bucket.
    for code in (
        "FOOD_AND_DRINK_COFFEE",
        "TRANSFER_OUT_ACCOUNT_TRANSFER",
        "TRANSFER_IN_DEPOSIT",
        "INCOME_WAGES",
    ):
        assert pfc.is_valid_detailed(code) is True
    assert pfc.is_valid_detailed("BOGUS_DETAILED_CODE") is False


def test_primary_of_maps_detailed_back_to_primary():
    for primary, detaileds in pfc.PFC_TAXONOMY.items():
        for d in detaileds:
            assert pfc.primary_of(d) == primary


def test_primary_of_unknown_returns_none():
    assert pfc.primary_of("BOGUS_DETAILED_CODE") is None


# ---------------------------------------------------------------------------
# side helpers
# ---------------------------------------------------------------------------

def test_primaries_for_side_partitions_correctly():
    spending = set(pfc.primaries_for_side("spending"))
    income = set(pfc.primaries_for_side("income"))
    assert spending.isdisjoint(income)
    assert "INCOME" in income
    assert "FOOD_AND_DRINK" in spending


# ---------------------------------------------------------------------------
# humanization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("code, expected", [
    ("FOOD_AND_DRINK", "Food and Drink"),
    ("GENERAL_MERCHANDISE", "General Merchandise"),
    ("TRANSPORTATION", "Transportation"),
])
def test_humanize_primary(code, expected):
    assert pfc.humanize_primary(code) == expected


def test_humanize_detailed_strips_primary_prefix():
    assert pfc.humanize_detailed("FOOD_AND_DRINK_COFFEE", "FOOD_AND_DRINK") == "Coffee"


def test_humanize_detailed_acronym_fixups():
    """The acronym table converts e.g. 'Tv' → 'TV' after title-casing."""
    # Look up a detailed code that contains "TV".
    for d in pfc._VALID_DETAILED:
        if "TV" in d:
            humanized = pfc.humanize_detailed(d)
            assert "Tv" not in humanized
            assert "TV" in humanized
            break
