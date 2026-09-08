from __future__ import annotations

import base64
import ctypes
import os
from ctypes import wintypes
from pathlib import Path
from typing import Protocol


class SecretStore(Protocol):
    def get(self, name: str) -> str | None: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class MemorySecretStore:
    """Test-only in-memory store."""
    def __init__(self) -> None:
        self._items: dict[str, str] = {}

    def get(self, name: str) -> str | None:
        return self._items.get(name)

    def set(self, name: str, value: str) -> None:
        self._items[name] = value

    def delete(self, name: str) -> None:
        self._items.pop(name, None)


class UnsupportedSecretStore:
    def _raise(self):
        raise RuntimeError("Secure credential persistence is only implemented for Windows in AI Bridge V1.2")

    def get(self, name: str) -> str | None:
        return None

    def set(self, name: str, value: str) -> None:
        self._raise()

    def delete(self, name: str) -> None:
        return None


if os.name == "nt":
    class _DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptProtectData.restype = wintypes.BOOL
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DATA_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_DATA_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DATA_BLOB),
    ]
    _crypt32.CryptUnprotectData.restype = wintypes.BOOL
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    _kernel32.LocalFree.restype = ctypes.c_void_p


class WindowsDpapiSecretStore:
    """Encrypts secrets with Windows DPAPI, scoped to the current Windows user."""
    def __init__(self, root: Path) -> None:
        if os.name != "nt":
            raise RuntimeError("Windows DPAPI secret store requires Windows")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        safe = "".join(ch if ch.isalnum() or ch in "_.-" else "_" for ch in name)
        return self.root / f"{safe}.dpapi"

    @staticmethod
    def _blob(data: bytes):
        buffer = ctypes.create_string_buffer(data)
        blob = _DATA_BLOB(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        return blob, buffer

    @staticmethod
    def _extract(blob) -> bytes:
        try:
            return ctypes.string_at(blob.pbData, blob.cbData)
        finally:
            if blob.pbData:
                _kernel32.LocalFree(blob.pbData)

    def set(self, name: str, value: str) -> None:
        raw = value.encode("utf-8")
        in_blob, keepalive = self._blob(raw)
        out_blob = _DATA_BLOB()
        if not _crypt32.CryptProtectData(
            ctypes.byref(in_blob), "AI Bridge credential", None, None, None, 0x1, ctypes.byref(out_blob)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        encrypted = self._extract(out_blob)
        path = self._path(name)
        path.write_bytes(base64.b64encode(encrypted))
        try:
            path.chmod(0o600)
        except OSError:
            pass
        del keepalive

    def get(self, name: str) -> str | None:
        path = self._path(name)
        if not path.exists():
            return None
        encrypted = base64.b64decode(path.read_bytes())
        in_blob, keepalive = self._blob(encrypted)
        out_blob = _DATA_BLOB()
        if not _crypt32.CryptUnprotectData(
            ctypes.byref(in_blob), None, None, None, None, 0x1, ctypes.byref(out_blob)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        raw = self._extract(out_blob)
        del keepalive
        return raw.decode("utf-8")

    def delete(self, name: str) -> None:
        try:
            self._path(name).unlink()
        except FileNotFoundError:
            pass


def default_secret_store(data_dir: Path) -> SecretStore:
    if os.name == "nt":
        return WindowsDpapiSecretStore(Path(data_dir) / "secrets")
    return UnsupportedSecretStore()
