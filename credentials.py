"""Current-user Windows DPAPI storage; never stores an unencrypted API credential."""
import ctypes
import json
import os
from pathlib import Path
from ctypes import wintypes

DEFAULT_PATH = Path(__file__).resolve().parent / ".private" / "connection.dpapi"


class Blob(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def transform(data, decrypt=False):
    if os.name != "nt":
        raise RuntimeError("此加密配置功能需要 Windows。")
    backing = ctypes.create_string_buffer(data)
    incoming = Blob(len(data), ctypes.cast(backing, ctypes.POINTER(ctypes.c_ubyte)))
    outgoing = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    if decrypt:
        function = crypt.CryptUnprotectData
        function.argtypes = [ctypes.POINTER(Blob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = function(ctypes.byref(incoming), None, None, None, None, 1, ctypes.byref(outgoing))
    else:
        function = crypt.CryptProtectData
        function.argtypes = [ctypes.POINTER(Blob), wintypes.LPCWSTR, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(Blob)]
        ok = function(ctypes.byref(incoming), "DeepSeek SolidCraft connection", None, None, None, 1, ctypes.byref(outgoing))
    if not ok:
        raise RuntimeError("Windows 无法加密或解密连接配置；请在当前账户重新保存。")
    try:
        return ctypes.string_at(outgoing.data, outgoing.size)
    finally:
        kernel.LocalFree(ctypes.cast(outgoing.data, ctypes.c_void_p))


def save(api_key, model, thinking, reasoning_effort="high", path=DEFAULT_PATH):
    if not api_key.strip():
        raise ValueError("请填写 API Key 后保存配置。")
    path=Path(path)
    payload=json.dumps({"api_key":api_key.strip(),"model":model,"thinking":bool(thinking),"reasoning_effort":reasoning_effort},ensure_ascii=False).encode("utf-8")
    encrypted=transform(payload)
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(".pending")
    temporary.write_bytes(encrypted)
    temporary.replace(path)


def load(path=DEFAULT_PATH):
    path=Path(path)
    if not path.is_file():
        return {}
    return json.loads(transform(path.read_bytes(),decrypt=True).decode("utf-8"))


def clear(path=DEFAULT_PATH):
    Path(path).unlink(missing_ok=True)
