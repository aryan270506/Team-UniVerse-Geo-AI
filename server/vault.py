"""Encryption for voucher codes (they are money): Fernet (AES-128-CBC + HMAC-SHA256).

Key: TERRATRACE_VOUCHER_KEY, else data/voucher.key (generated once, mode 0600, git-ignored).
Lose the key and stored codes can't be read; back it up with the database.
Duplicates are detected by a keyed hash, so the plain code is never stored.
"""
import hashlib
import hmac
import os
import re
import threading

from cryptography.fernet import Fernet, InvalidToken

from hazardmap.config import ROOT

KEY_PATH = ROOT / "data" / "voucher.key"
_lock = threading.Lock()
_fernet: Fernet | None = None
_hkey: bytes | None = None


def _load():
    global _fernet, _hkey
    with _lock:
        if _fernet is None:
            key = os.environ.get("TERRATRACE_VOUCHER_KEY", "").encode()
            if not key:
                if not KEY_PATH.exists():
                    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
                    fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as f:
                        f.write(Fernet.generate_key())
                key = KEY_PATH.read_bytes().strip()
            _fernet = Fernet(key)
            _hkey = hashlib.sha256(b"terratrace-voucher-dedupe:" + key).digest()
    return _fernet, _hkey


def normalise(code: str) -> str:
    return re.sub(r"[\s\-]", "", code).upper()


def encrypt(text: str | None) -> str | None:
    return None if not text else _load()[0].encrypt(text.encode()).decode()


def decrypt(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _load()[0].decrypt(token.encode()).decode()
    except InvalidToken:
        raise RuntimeError("Voucher key doesn't match the stored codes (data/voucher.key changed?)")


def code_hash(code: str) -> str:
    return hmac.new(_load()[1], normalise(code).encode(), hashlib.sha256).hexdigest()


def mask(last4: str) -> str:
    return f"••••-••••-{last4}"
