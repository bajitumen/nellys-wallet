"""Fernet encryption wrappers for sensitive fields (Plaid tokens, etc.)."""

from cryptography.fernet import Fernet

import config

_fernet = Fernet(config.FERNET_KEY.encode())


def encrypt(plaintext: str) -> bytes:
    """Encrypt a string. Returns bytes suitable for a LargeBinary column."""
    return _fernet.encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    """Decrypt bytes from the DB back into a string."""
    return _fernet.decrypt(ciphertext).decode()
