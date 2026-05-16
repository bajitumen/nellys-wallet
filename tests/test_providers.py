"""Tests for providers: per-item fetch behavior and caching."""

from unittest.mock import MagicMock, patch

import pytest


def _mock_account(acct_type, subtype, balance=100.0):
    a = MagicMock()
    a.type = acct_type
    a.subtype = subtype
    a.name = "Test Account"
    a.mask = "1234"
    a.account_id = "acct1"
    a.balances.current = balance
    a.balances.available = balance
    return a


@pytest.fixture
def patch_plaid_client():
    """Patch providers.plaid_client_for to return a MagicMock client."""
    with patch("providers.plaid_client_for") as mock_for:
        client = MagicMock()
        mock_for.return_value = client
        yield client


def test_fetch_one_buckets_accounts(user_with_item, patch_plaid_client):
    """Cash, credit, investment, other accounts all land in the right bucket."""
    from providers import _fetch_one
    accounts_resp = MagicMock()
    accounts_resp.accounts = [
        _mock_account("depository", "checking", balance=500.0),
        _mock_account("credit", "credit card", balance=100.0),
        _mock_account("investment", "brokerage", balance=1000.0),
        _mock_account("loan", "mortgage", balance=200.0),
    ]
    patch_plaid_client.accounts_get.return_value = accounts_resp
    result = _fetch_one(patch_plaid_client, user_with_item.items[0])
    assert len(result["cash"]) == 1
    assert len(result["credit"]) == 1
    assert len(result["investment"]) == 1
    assert len(result["other"]) == 1
    assert result["errors"] == []


def test_fetch_all_caches_results(user_with_item, patch_plaid_client):
    """Second call within TTL hits the cache; client.accounts_get only runs once."""
    from providers import fetch_all
    accounts_resp = MagicMock()
    accounts_resp.accounts = [_mock_account("depository", "checking")]
    patch_plaid_client.accounts_get.return_value = accounts_resp
    fetch_all(user_with_item)
    fetch_all(user_with_item)
    fetch_all(user_with_item)
    assert patch_plaid_client.accounts_get.call_count == 1


def test_fetch_all_force_refresh_bypasses_cache(user_with_item, patch_plaid_client):
    """`force_refresh=True` skips the cache check."""
    from providers import fetch_all
    accounts_resp = MagicMock()
    accounts_resp.accounts = [_mock_account("depository", "checking")]
    patch_plaid_client.accounts_get.return_value = accounts_resp
    fetch_all(user_with_item)
    fetch_all(user_with_item, force_refresh=True)
    assert patch_plaid_client.accounts_get.call_count == 2


def test_invalidate_cache_drops_entry(user_with_item, patch_plaid_client):
    """`invalidate_cache(user_id)` forces the next call to refetch."""
    from providers import fetch_all, invalidate_cache
    accounts_resp = MagicMock()
    accounts_resp.accounts = [_mock_account("depository", "checking")]
    patch_plaid_client.accounts_get.return_value = accounts_resp
    fetch_all(user_with_item)
    invalidate_cache(user_with_item.id)
    fetch_all(user_with_item)
    assert patch_plaid_client.accounts_get.call_count == 2


def test_fetch_all_with_no_items(user, patch_plaid_client):
    """No linked items → empty result, no Plaid calls."""
    from providers import fetch_all
    result = fetch_all(user)
    assert result["cash"] == []
    patch_plaid_client.accounts_get.assert_not_called()


def test_fetch_one_includes_logo(user_with_item, patch_plaid_client, db_session):
    """`_fetch_one` passes the linked item's logo through to each account dict."""
    from providers import _fetch_one
    item = user_with_item.items[0]
    item.logo = "BASE64_PNG_DATA"
    db_session.commit()
    accounts_resp = MagicMock()
    accounts_resp.accounts = [_mock_account("depository", "checking")]
    patch_plaid_client.accounts_get.return_value = accounts_resp
    result = _fetch_one(patch_plaid_client, item)
    assert result["cash"][0]["logo"] == "BASE64_PNG_DATA"


def test_source_logos_returns_per_institution_map(user_with_item, db_session):
    """source_logos collects {institution_name: logo} across the user's items."""
    from providers import source_logos
    user_with_item.items[0].logo = "LOGO_A"
    db_session.commit()
    logos = source_logos(user_with_item)
    assert logos == {"TestBank": "LOGO_A"}


def test_institution_letter_color_uses_brand_when_available():
    """Plaid's primary_color is preferred over the hash-based fallback."""
    from providers import institution_letter_color
    assert institution_letter_color("Chase", "#095aa6") == "#095aa6"


def test_institution_letter_color_falls_back_to_hash():
    """No brand color → stable hash-derived palette color."""
    from providers import institution_letter_color
    c1 = institution_letter_color("Chase", None)
    c2 = institution_letter_color("Chase", "")
    assert c1.startswith("#") and len(c1) == 7
    assert c1 == c2  # stable across calls


def test_source_avatars_pairs_logo_and_color(user_with_item, db_session):
    """source_avatars exposes both logo + primary_color per institution."""
    from providers import source_avatars
    user_with_item.items[0].logo = "BASE64"
    user_with_item.items[0].primary_color = "#095aa6"
    db_session.commit()
    av = source_avatars(user_with_item)
    assert av["TestBank"] == {"logo": "BASE64", "primary_color": "#095aa6"}


def test_humanize_account_type():
    """Title-case for common types, explicit overrides for acronyms."""
    from providers import humanize_account_type
    assert humanize_account_type("checking") == "Checking"
    assert humanize_account_type("credit card") == "Credit Card"
    assert humanize_account_type("money market") == "Money Market"
    # Acronyms preserved.
    assert humanize_account_type("ira") == "IRA"
    assert humanize_account_type("hsa") == "HSA"
    assert humanize_account_type("cd") == "CD"
    # Mixed: SEP IRA, Roth IRA.
    assert humanize_account_type("sep ira") == "SEP IRA"
    assert humanize_account_type("roth ira") == "Roth IRA"
    # 401k → 401(k).
    assert humanize_account_type("401k") == "401(k)"
    assert humanize_account_type("403b") == "403(b)"
    # Empty / None safe.
    assert humanize_account_type("") == ""


def test_classify_account_buckets():
    """Smoke test for the account classifier."""
    from providers import _classify
    assert _classify(_mock_account("depository", "checking")) == "cash"
    assert _classify(_mock_account("credit", "credit card")) == "credit"
    assert _classify(_mock_account("investment", "brokerage")) == "investment"
    assert _classify(_mock_account("loan", "mortgage")) == "other"
