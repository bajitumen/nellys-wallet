"""At-rest encryption invariants for code/crypto.py and the Plaid persist path.

Mock-tests already verify exchange_and_save's API contract; these assert the
real ciphertext semantics nothing in the codebase was previously checking.
"""

from unittest.mock import MagicMock, patch

import pytest
from cryptography.fernet import Fernet, InvalidToken

import crypto
from models import PlaidItem


def test_decrypt_roundtrips_encrypt():
    plaintext = "access-sandbox-abc123"
    assert crypto.decrypt(crypto.encrypt(plaintext)) == plaintext


def test_ciphertext_is_not_plaintext():
    plaintext = "access-sandbox-abc123"
    ciphertext = crypto.encrypt(plaintext)
    assert plaintext.encode() not in ciphertext
    assert ciphertext != plaintext.encode()


def test_ciphertext_is_nondeterministic():
    plaintext = "the-same-value"
    a = crypto.encrypt(plaintext)
    b = crypto.encrypt(plaintext)
    assert a != b, "Fernet ciphertexts must include a fresh IV per encrypt"
    assert crypto.decrypt(a) == crypto.decrypt(b) == plaintext


def test_wrong_key_cannot_decrypt(monkeypatch):
    plaintext = "access-sandbox-abc123"
    ciphertext = crypto.encrypt(plaintext)

    other_key = Fernet.generate_key()
    monkeypatch.setattr(crypto, "_fernet", Fernet(other_key))

    with pytest.raises(InvalidToken):
        crypto.decrypt(ciphertext)


def test_plaid_item_persists_encrypted_token(db_session, user):
    """exchange_and_save must store a ciphertext, never the raw token."""
    raw_access_token = "access-sandbox-supersecret-deadbeef"

    fake_client = MagicMock()
    fake_client.item_public_token_exchange.return_value = MagicMock(
        access_token=raw_access_token, item_id="item_id_123",
    )

    with patch("plaid_link.lookup_institution", return_value={"name": "Bank"}):
        import plaid_link
        item = plaid_link.exchange_and_save(
            fake_client, db_session, user, "public-sandbox-token",
        )

    persisted = db_session.get(PlaidItem, item.id)
    raw_bytes = persisted.access_token_encrypted
    assert raw_access_token.encode() not in raw_bytes
    assert raw_bytes != raw_access_token.encode()
    assert persisted.get_access_token() == raw_access_token
