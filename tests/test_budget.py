"""Tests for budget helpers and the /budget routes."""


def test_get_budgets_empty(user, db_session):
    from budget import get_budgets
    assert get_budgets(user, db_session) == {}


def test_upsert_creates_then_updates(user, db_session):
    from budget import get_budgets, upsert
    upsert(user, "FOOD_AND_DRINK_COFFEE", 50.0, db_session)
    assert get_budgets(user, db_session) == {"FOOD_AND_DRINK_COFFEE": 50.0}
    upsert(user, "FOOD_AND_DRINK_COFFEE", 75.0, db_session)
    assert get_budgets(user, db_session) == {"FOOD_AND_DRINK_COFFEE": 75.0}


def test_clear_removes_row(user, db_session):
    from budget import clear, get_budgets, upsert
    upsert(user, "FOOD_AND_DRINK_COFFEE", 50.0, db_session)
    clear(user, "FOOD_AND_DRINK_COFFEE", db_session)
    assert get_budgets(user, db_session) == {}


def test_primary_sum_aggregates_subcategories(user, db_session):
    from budget import primary_sum, upsert
    upsert(user, "FOOD_AND_DRINK_COFFEE", 50.0, db_session)
    upsert(user, "FOOD_AND_DRINK_GROCERIES", 200.0, db_session)
    upsert(user, "TRANSPORTATION_GAS", 100.0, db_session)
    assert primary_sum(user, "FOOD_AND_DRINK", db_session) == 250.0
    assert primary_sum(user, "TRANSPORTATION", db_session) == 100.0
    assert primary_sum(user, "MEDICAL", db_session) == 0.0


def test_build_groups_includes_every_primary_in_order(user, db_session):
    from budget import build_groups, get_budgets
    import pfc
    groups = build_groups(get_budgets(user, db_session))
    assert [g["primary"] for g in groups] == list(pfc.PFC_TAXONOMY.keys())
    # All sub-items show with amount 0 when no budgets are set.
    for g in groups:
        assert g["total"] == 0.0
        assert len(g["subitems"]) == len(pfc.PFC_TAXONOMY[g["primary"]])
        for s in g["subitems"]:
            assert s["amount"] == 0.0


def test_build_groups_attaches_palette_color(user, db_session):
    """Each group carries the palette color of its primary category."""
    from budget import build_groups, get_budgets
    import pfc
    groups = {g["primary"]: g for g in build_groups(get_budgets(user, db_session))}
    assert groups["FOOD_AND_DRINK"]["color"] == pfc.CATEGORY_COLORS["FOOD_AND_DRINK"]
    assert groups["TRANSPORTATION"]["color"] == pfc.CATEGORY_COLORS["TRANSPORTATION"]


def test_humanize_primary_keeps_and_lowercase():
    import pfc
    assert pfc.humanize_primary("FOOD_AND_DRINK") == "Food and Drink"
    assert pfc.humanize_primary("GOVERNMENT_AND_NON_PROFIT") == "Government and Non Profit"
    assert pfc.humanize_primary("TRAVEL") == "Travel"


def test_humanize_detailed_strips_primary_prefix():
    import pfc
    assert pfc.humanize_detailed("FOOD_AND_DRINK_FAST_FOOD") == "Fast Food"
    assert pfc.humanize_detailed("TRANSPORTATION_GAS") == "Gas"
    # OTHER_<PRIMARY> collapses to just "Other".
    assert pfc.humanize_detailed("FOOD_AND_DRINK_OTHER_FOOD_AND_DRINK") == "Other"
    # Two-item lists keep "and" without commas.
    assert pfc.humanize_detailed("TRANSPORTATION_BIKES_AND_SCOOTERS") == "Bikes and Scooters"


def test_humanize_detailed_oxford_commas_for_three_item_lists():
    import pfc
    assert pfc.humanize_detailed("FOOD_AND_DRINK_BEER_WINE_AND_LIQUOR") == "Beer, Wine, and Liquor"
    assert pfc.humanize_detailed(
        "ENTERTAINMENT_SPORTING_EVENTS_AMUSEMENT_PARKS_AND_MUSEUMS"
    ) == "Sporting Events, Amusement Parks, and Museums"


def test_humanize_preserves_known_acronyms():
    """TV and ATM survive title-casing intact instead of becoming Tv/Atm."""
    import pfc
    assert pfc.humanize_detailed("ENTERTAINMENT_TV_AND_MOVIES") == "TV and Movies"
    assert pfc.humanize_detailed("BANK_FEES_ATM_FEES") == "ATM Fees"


def test_is_valid_detailed():
    import pfc
    assert pfc.is_valid_detailed("FOOD_AND_DRINK_COFFEE") is True
    assert pfc.is_valid_detailed("BOGUS") is False
    # Excluded categories aren't in the taxonomy.
    assert pfc.is_valid_detailed("INCOME_WAGES") is False


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

def test_budget_view_no_user(client):
    r = client.get("/budget")
    assert r.status_code == 200
    assert b"No user provisioned" in r.data


def test_budget_view_renders_groups(client, user_with_item):
    r = client.get("/budget")
    assert r.status_code == 200
    assert b"Food and Drink" in r.data
    assert b"Coffee" in r.data
    # Primary total starts at $0.00 for a fresh user.
    assert b"$0.00" in r.data


def test_budget_save_creates_row(client, user_with_item, db_session):
    from models import Budget
    r = client.post("/budget/FOOD_AND_DRINK_COFFEE", json={"amount": 50.0})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert body["primary_sum"] == 50.0
    rows = db_session.query(Budget).all()
    assert len(rows) == 1
    assert rows[0].pfc_detailed == "FOOD_AND_DRINK_COFFEE"
    assert rows[0].amount == 50.0


def test_budget_save_returns_running_primary_sum(client, user_with_item):
    client.post("/budget/FOOD_AND_DRINK_COFFEE", json={"amount": 50.0})
    r = client.post("/budget/FOOD_AND_DRINK_GROCERIES", json={"amount": 200.0})
    assert r.get_json()["primary_sum"] == 250.0


def test_budget_save_null_amount_clears(client, user_with_item, db_session):
    from models import Budget
    client.post("/budget/FOOD_AND_DRINK_COFFEE", json={"amount": 50.0})
    r = client.post("/budget/FOOD_AND_DRINK_COFFEE", json={"amount": None})
    assert r.status_code == 200
    assert db_session.query(Budget).count() == 0


def test_budget_save_rejects_unknown_detailed(client, user_with_item):
    r = client.post("/budget/BOGUS_CATEGORY", json={"amount": 10.0})
    assert r.status_code == 400


def test_budget_save_rejects_negative(client, user_with_item):
    r = client.post("/budget/FOOD_AND_DRINK_COFFEE", json={"amount": -5.0})
    assert r.status_code == 400


def test_budget_save_rejects_non_numeric(client, user_with_item):
    r = client.post("/budget/FOOD_AND_DRINK_COFFEE", json={"amount": "not-a-number"})
    assert r.status_code == 400


def test_budget_save_no_user(client):
    r = client.post("/budget/FOOD_AND_DRINK_COFFEE", json={"amount": 10.0})
    assert r.status_code == 400
