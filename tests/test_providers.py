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


def test_classify_account_buckets():
    """Smoke test for the account classifier."""
    from providers import _classify
    assert _classify(_mock_account("depository", "checking")) == "cash"
    assert _classify(_mock_account("credit", "credit card")) == "credit"
    assert _classify(_mock_account("investment", "brokerage")) == "investment"
    assert _classify(_mock_account("loan", "mortgage")) == "other"
