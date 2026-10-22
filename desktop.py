"""Open/reuse a visible SolidCraft control window for MCP operations."""
import asyncio
import ctypes
import os
import socket
import subprocess
from ctypes import wintypes
from pathlib import Path


class Desktop:
    def __init__(self, executable, endpoint, log_path):
        self.executable=Path(executable)
        host,port=endpoint.rsplit(":",1)
        if host not in {"127.0.0.1","localhost"}:
            raise ValueError("桌面可见模式仅连接本机控制端口。")
        self.host,self.port=host,int(port)
        self.log_path=Path(log_path)
        self.process=None
        self.pid_path=self.log_path.with_suffix(".pid")
        try:
            self.window_pid=int(self.pid_path.read_text().strip())
        except (OSError,ValueError):
            self.window_pid=None

    def listening(self):
        try:
            with socket.create_connection((self.host,self.port),timeout=.2):
                return True
        except OSError:
            return False

    async def ensure(self):
        if self.listening():
            return False
        if not self.executable.is_file():
            raise FileNotFoundError(f"找不到 SolidCraft 桌面程序：{self.executable}")
        self.log_path.parent.mkdir(parents=True,exist_ok=True)
        with self.log_path.open("ab") as output:
            # The user explicitly requested a visible interactive CAD window.
            self.process=subprocess.Popen([str(self.executable),"--control",str(self.port)],stdout=output,stderr=output,cwd=str(self.executable.parent))
        self.window_pid=self.process.pid
        self.pid_path.write_text(str(self.window_pid),encoding="ascii")
        for _ in range(100):
            if self.listening():
                self.focus()
                return True
            if self.process.poll() is not None:
                raise RuntimeError("SolidCraft 桌面程序退出，请查看 desktop.log。")
            await asyncio.sleep(.2)
        raise RuntimeError("SolidCraft 桌面控制端口启动超时。")

    def focus(self):
        if os.name!="nt" or self.window_pid is None:
            return
        pid=self.window_pid
        user=ctypes.windll.user32
        callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        user.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        user.IsWindowVisible.argtypes=[wintypes.HWND]
        user.ShowWindow.argtypes=[wintypes.HWND,ctypes.c_int]
        user.SetForegroundWindow.argtypes=[wintypes.HWND]
        user.EnumWindows.argtypes=[callback_type,wintypes.LPARAM]
        def callback(window,lparam):
            found=wintypes.DWORD()
            user.GetWindowThreadProcessId(window,ctypes.byref(found))
            if found.value==pid and user.IsWindowVisible(window):
                user.ShowWindow(window,9)
                user.SetForegroundWindow(window)
                return False
            return True
        user.EnumWindows(callback_type(callback),0)
