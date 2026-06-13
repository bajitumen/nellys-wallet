from cryptography.fernet import Fernet, MultiFernet

import config


def _build_multifernet() -> MultiFernet:
    keys = [Fernet(config.FERNET_KEY.encode())]
    for old in config.FERNET_KEY_OLD:
        keys.append(Fernet(old.encode()))
    return MultiFernet(keys)


_fernet = _build_multifernet()


def encrypt(plaintext: str) -> bytes:
    return _fernet.encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return _fernet.decrypt(ciphertext).decode()


def rotate(ciphertext: bytes) -> bytes:
    return _fernet.rotate(ciphertext)
