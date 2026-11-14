"""Encrypt saved passwords and tokens at rest with the operating system's own protection.

On Windows this is DPAPI (CryptProtectData): the secret can only be decrypted by the same Windows user on the same computer, so copying
`data/chat.db` (or an export) to another machine or user does not reveal it. No key is stored by this app, so there is nothing to leak.
On other systems there is no equivalent built in, so values are stored as they are (documented in the README). Anything that was saved
before this existed still works: values without the prefix are read as plain text and are protected the next time they are saved."""
from __future__ import annotations

import base64
import sys

PREFIX = "dpapi:"


def available() -> bool:
    return sys.platform == "win32"


def _blob(data: bytes):
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    buf = ctypes.create_string_buffer(data, len(data))
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), buf, DATA_BLOB


def _apply(fn_name: str, data: bytes) -> bytes:
    import ctypes
    inb, keep, Blob = _blob(data)
    out = Blob()
    fn = getattr(ctypes.windll.crypt32, fn_name)
    if not fn(ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(out)):  # both calls share this signature
        raise OSError(f"{fn_name} failed")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)
        del keep


def protect(text: str) -> str:
    """Encrypt a secret for storage (a no-op when already protected, empty, or not on Windows)."""
    if not text or text.startswith(PREFIX) or not available():
        return text
    try:
        return PREFIX + base64.b64encode(_apply("CryptProtectData", text.encode("utf-8"))).decode("ascii")
    except Exception:  # noqa: BLE001 - never lose a secret because encryption was unavailable: keep it readable instead
        return text


def reveal(text: str) -> str:
    """The original secret ('' when it cannot be decrypted, e.g. copied from another computer or Windows user)."""
    if not text or not text.startswith(PREFIX):
        return text
    if not available():
        return ""
    try:
        return _apply("CryptUnprotectData", base64.b64decode(text[len(PREFIX):])).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""
