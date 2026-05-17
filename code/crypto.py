from cryptography.fernet import Fernet

import config

_fernet = Fernet(config.FERNET_KEY.encode())


def encrypt(plaintext: str) -> bytes:
    return _fernet.encrypt(plaintext.encode())


def decrypt(ciphertext: bytes) -> str:
    return _fernet.decrypt(ciphertext).decode()
