"""
app_controller.py
第4组：应用操作控制器（跨平台抽象层）

为 Agentic OS 提供桌面应用控制能力，使上层 Agent 不仅能管理文件系统，
还能像真实用户一样操作桌面应用（窗口查找/激活、快捷键、文本输入、截图）。

设计要点：
- 抽象基类 BaseAppController 定义统一接口，
  WindowsAppController / LinuxAppController 分别实现。
- get_controller() 按 platform.system() 选择实现，与 core/utils.py
  的跨平台路径适配保持同一模式。
- 每个动作都返回可 JSON 序列化的结果，失败抛异常由 ToolRegistry 统一捕获。
"""

import platform
import subprocess
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path


class BaseAppController(ABC):
    """应用控制器抽象基类"""

    @abstractmethod
    def list_windows(self):
        """
        列出所有可见窗口

        返回：
            [{"hwnd": int, "title": str, "pid": int}, ...]
        """

    @abstractmethod
    def find_window(self, title=None, process=None):
        """
        按标题或进程名查找窗口

        参数：
            title:   窗口标题子串（忽略大小写）
            process: 进程名（如 "QQ"、"qq.exe"、"chrome"）

        返回：
            匹配到的第一个窗口 {"hwnd", "title", "pid"}；找不到返回 None
        """

    @abstractmethod
    def activate_window(self, hwnd):
        """
        把指定窗口置于前台

        参数：
            hwnd: 窗口句柄（find_window/list_windows 返回的整数）

        返回：
            True 表示成功；内部应做容错（如 AttachThreadInput）
        """

    @abstractmethod
    def send_hotkey(self, keys):
        """
        发送快捷键组合

        参数：
            keys: 键名列表或字符串，如 ["ctrl", "f"]、["alt", "tab"]
        """

    @abstractmethod
    def send_text(self, text):
        """
        向当前焦点输入文本（中文安全，走剪贴板粘贴）
        """

    @abstractmethod
    def take_screenshot(self, output=None):
        """
        截取当前屏幕

        参数：
            output: 保存路径；未指定时保存到系统临时目录

        返回：
            截图文件的绝对路径
        """

    @abstractmethod
    def click_at(self, x, y):
        """
        在屏幕坐标处左键单击一次

        参数：
            x, y: 屏幕物理像素坐标（左上角为原点），与 take_screenshot 同坐标系

        用途：
            QQ 等自绘 UI 的搜索框 / 结果行需鼠标点击才能聚焦或选中；
            键盘回车常常落到"查看资料"而非进入会话。由截图 + 视觉定位给出坐标后点它。
        """

    @abstractmethod
    def read_clipboard(self):
        """
        读取当前剪贴板文本（支持中文）

        返回：
            剪贴板中的字符串；为空 / 非文本 / 读取失败时返回 ""
        """

    @abstractmethod
    def get_window_rect(self, hwnd):
        """
        获取窗口的屏幕坐标外接矩形（与 click_at / take_screenshot 同一坐标系）

        参数：
            hwnd: 窗口句柄

        返回：
            {"left", "top", "right", "bottom", "width", "height"}

        用途：
            探针扫描 / 按窗口比例定位 UI 区域时需要以窗口矩形为锚，
            否则只凭绝对坐标在窗口位置变化后会失效。
        """


class WindowsAppController(BaseAppController):
    """
    Windows 实现：ctypes 调 user32.dll / kernel32.dll

    窗口查找 / 激活基于 Win32 窗口管理 API，快捷键基于 keybd_event，
    文本输入走剪贴板（CF_UNICODETEXT 支持中文） + Ctrl+V。
    """

    # 键名 -> 虚拟键码（仅覆盖常用键，未知键抛错提示）
    VK_CODES = {
        "ctrl": 0x11, "control": 0x11,
        "alt": 0x12, "menu": 0x12,
        "shift": 0x10,
        "win": 0x5B, "cmd": 0x5B, "super": 0x5B,
        "enter": 0x0D, "return": 0x0D,
        "esc": 0x1B, "escape": 0x1B,
        "tab": 0x09,
        "space": 0x20,
        "backspace": 0x08,
        "delete": 0x2E, "del": 0x2E,
        "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
        "home": 0x24, "end": 0x23,
        "pageup": 0x21, "pagedown": 0x22,
    }

    def __init__(self):
        import ctypes
        from ctypes import wintypes
        self._ctypes = ctypes
        self._wintypes = wintypes
        self._user32 = ctypes.windll.user32
        self._kernel32 = ctypes.windll.kernel32
        # 补充字母 / 数字 / F 键
        for ch in "abcdefghijklmnopqrstuvwxyz":
            self.VK_CODES[ch] = ord(ch.upper())
        for n in "0123456789":
            self.VK_CODES[n] = ord(n)
        for i in range(1, 13):
            self.VK_CODES[f"f{i}"] = 0x70 + (i - 1)

        # DPI 感知：让 SetCursorPos 点击坐标与 ImageGrab 截图像素 1:1，
        # 否则系统缩放 != 100% 时"视觉定位的坐标"和"实际点击位置"会错位。
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # 进程按监视器感知 DPI
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass

        # HGLOBAL / LPVOID 均为指针宽度；缺省 restype=c_int 会把 64 位句柄截断成 32 位，
        # 导致 GlobalAlloc 返回值损坏、GlobalLock 报 "GlobalLock 失败"，
        # 以及 GlobalUnlock/GlobalFree 传大整数时 OverflowError。
        u32 = self._user32
        k32 = self._kernel32
        k32.GlobalAlloc.restype = ctypes.c_void_p
        k32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        k32.GlobalLock.restype = ctypes.c_void_p
        k32.GlobalLock.argtypes = [ctypes.c_void_p]
        k32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        k32.GlobalFree.restype = ctypes.c_void_p
        k32.GlobalFree.argtypes = [ctypes.c_void_p]
        u32.OpenClipboard.argtypes = [ctypes.c_void_p]
        u32.SetClipboardData.restype = ctypes.c_void_p
        u32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]
        u32.EmptyClipboard.restype = wintypes.BOOL
        u32.CloseClipboard.restype = wintypes.BOOL

    # =====================================================
    # 窗口枚举 / 查找
    # =====================================================

    def list_windows(self):
        ctypes = self._ctypes
        wintypes = self._wintypes
        user32 = self._user32
        results = []

        EnumWindowsProc = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        @EnumWindowsProc
        def _callback(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            results.append({
                "hwnd": int(hwnd),
                "title": buf.value,
                "pid": int(pid.value),
            })
            return True

        user32.EnumWindows(_callback, 0)
        return results

    def find_window(self, title=None, process=None):
        windows = self.list_windows()
        for win in windows:
            if title is not None:
                if title.lower() not in win["title"].lower():
                    continue
            if process is not None:
                pname = self._pid_to_name(win["pid"])
                if pname is None:
                    continue
                if process.lower().replace(".exe", "") not in pname.lower():
                    continue
            return win
        return None

    def _pid_to_name(self, pid):
        """用 tasklist 反查进程名（不含扩展名时返回小写名）"""
        try:
            out = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                capture_output=True, text=True, timeout=5,
            ).stdout
        except Exception:
            return None
        for line in out.splitlines():
            line = line.strip()
            if line and line.startswith('"'):
                name = line.split('"')[1]
                return name
        return None

    # =====================================================
    # 窗口激活（含前台锁定容错）
    # =====================================================

    def activate_window(self, hwnd):
        user32 = self._user32
        kernel32 = self._kernel32
        hwnd = int(hwnd)

        # 最小化则先恢复
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            time.sleep(0.2)

        # 尝试 1：直接 SetForegroundWindow
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.3)
        if int(user32.GetForegroundWindow()) == hwnd:
            return True

        # 尝试 2：AttachThreadInput + Alt 键 trick 绕过前台锁定
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        cur_thread = kernel32.GetCurrentThreadId()
        attached = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))

        user32.keybd_event(0x12, 0, 0, 0)  # Alt 按下
        user32.keybd_event(0x12, 0, 2, 0)  # Alt 抬起
        user32.SetForegroundWindow(hwnd)

        if attached:
            user32.AttachThreadInput(cur_thread, fg_thread, False)
        time.sleep(0.3)

        return int(user32.GetForegroundWindow()) == hwnd

    # =====================================================
    # 快捷键 / 文本输入
    # =====================================================

    def _vk(self, key):
        code = self.VK_CODES.get(str(key).lower())
        if code is None:
            raise ValueError(f"未知按键: {key}")
        return code

    def send_hotkey(self, keys):
        user32 = self._user32
        if isinstance(keys, str):
            keys = [keys]
        codes = [self._vk(k) for k in keys]

        for c in codes[:-1]:
            user32.keybd_event(c, 0, 0, 0)
            time.sleep(0.02)
        main = codes[-1]
        user32.keybd_event(main, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(main, 0, 2, 0)
        for c in reversed(codes[:-1]):
            user32.keybd_event(c, 0, 2, 0)
        time.sleep(0.05)

    def send_text(self, text):
        self._set_clipboard(text)
        time.sleep(0.05)
        self.send_hotkey(["ctrl", "v"])
        time.sleep(0.05)

    def _set_clipboard(self, text):
        """通过 Win32 剪贴板 API 写入文本（CF_UNICODETEXT 支持中文）"""
        ctypes = self._ctypes
        user32 = self._user32
        kernel32 = self._kernel32

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002

        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            raise OSError("GlobalAlloc 失败")
        try:
            ptr = kernel32.GlobalLock(handle)
            if not ptr:
                raise OSError("GlobalLock 失败")
            ctypes.memmove(ptr, data, len(data))
            kernel32.GlobalUnlock(handle)

            if not user32.OpenClipboard(None):
                raise OSError("打开剪贴板失败")
            try:
                user32.EmptyClipboard()
                if not user32.SetClipboardData(CF_UNICODETEXT, handle):
                    raise OSError("写入剪贴板失败")
            finally:
                user32.CloseClipboard()
            # 成功后系统拥有该内存，置 None 避免 GlobalFree
            handle = None
        finally:
            if handle is not None:
                kernel32.GlobalFree(handle)

    def set_clipboard(self, text):
        """把文本写入剪贴板（公开入口；send_text 内部复用它做粘贴源）"""
        self._set_clipboard(text)

    # =====================================================
    # 截图
    # =====================================================

    def take_screenshot(self, output=None):
        try:
            from PIL import ImageGrab
        except ImportError:
            raise RuntimeError("截图需要 Pillow，请先 pip install Pillow")
        if output is None:
            output = Path(tempfile.gettempdir()) / f"agentic_screenshot_{int(time.time() * 1000)}.png"
        else:
            output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        ImageGrab.grab().save(str(output))
        return str(output.absolute())

    def click_at(self, x, y):
        """移动鼠标到屏幕坐标并左键单击（DPI 感知已开启，坐标与截图 1:1）"""
        user32 = self._user32
        user32.SetCursorPos(int(x), int(y))
        time.sleep(0.06)
        user32.mouse_event(0x0002, 0, 0, 0, 0)   # MOUSEEVENTF_LEFTDOWN
        time.sleep(0.03)
        user32.mouse_event(0x0004, 0, 0, 0, 0)   # MOUSEEVENTF_LEFTUP
        return f"已点击 ({x}, {y})"

    def read_clipboard(self):
        """读取剪贴板文本（CF_UNICODETEXT）；为空 / 非文本 / 失败返回 "" """
        ctypes = self._ctypes
        wintypes = self._wintypes
        user32 = self._user32
        kernel32 = self._kernel32
        user32.GetClipboardData.restype = ctypes.c_void_p
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        try:
            if not user32.OpenClipboard(None):
                return ""
            try:
                handle = user32.GetClipboardData(13)  # CF_UNICODETEXT
                if not handle:
                    return ""
                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    return ""
                try:
                    return ctypes.wstring_at(ptr) or ""
                finally:
                    kernel32.GlobalUnlock(handle)
            finally:
                user32.CloseClipboard()
        except Exception:
            return ""

    def get_window_rect(self, hwnd):
        """获取窗口屏幕矩形（GetWindowRect，物理像素，与截图/点击同坐标系）"""
        ctypes = self._ctypes
        user32 = self._user32

        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

        rect = RECT()
        if not user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
            raise OSError(f"GetWindowRect 失败: hwnd={hwnd}")
        return {
            "left": int(rect.left), "top": int(rect.top),
            "right": int(rect.right), "bottom": int(rect.bottom),
            "width": int(rect.right - rect.left),
            "height": int(rect.bottom - rect.top),
        }


class LinuxAppController(BaseAppController):
    """
    Linux 实现：subprocess 调 xdotool / wmctrl / gnome-screenshot

    依赖（缺失时给出明确提示）：
    - xdotool:  窗口查找/激活、快捷键、文本输入
    - wmctrl:   窗口枚举
    - gnome-screenshot / scrot: 截图
    """

    def _require(self, cmd):
        """检查命令是否可用"""
        try:
            subprocess.run(["which", cmd], capture_output=True, check=True)
        except Exception:
            raise RuntimeError(f"缺少命令 {cmd}，请先安装（如 sudo apt install {cmd}）")

    def list_windows(self):
        self._require("wmctrl")
        out = subprocess.run(
            ["wmctrl", "-lp"], capture_output=True, text=True, check=True,
        ).stdout
        results = []
        for line in out.splitlines():
            parts = line.split(None, 4)
            if len(parts) < 5:
                continue
            win_id, _desktop, pid, _host, title = parts
            results.append({
                "hwnd": int(win_id, 16),
                "title": title,
                "pid": int(pid),
            })
        return results

    def find_window(self, title=None, process=None):
        windows = self.list_windows()
        for win in windows:
            if title is not None and title.lower() not in win["title"].lower():
                continue
            if process is not None:
                pname = self._pid_to_name(win["pid"])
                if pname is None or process.lower() not in pname.lower():
                    continue
            return win
        return None

    def _pid_to_name(self, pid):
        try:
            with open(f"/proc/{pid}/comm", "r") as f:
                return f.read().strip()
        except Exception:
            return None

    def activate_window(self, hwnd):
        self._require("xdotool")
        try:
            subprocess.run(["xdotool", "windowactivate", str(hwnd)],
                           capture_output=True, check=True)
            return True
        except Exception:
            return False

    def send_hotkey(self, keys):
        self._require("xdotool")
        if isinstance(keys, str):
            keys = [keys]
        combo = "+".join(str(k) for k in keys)
        subprocess.run(["xdotool", "key", combo], capture_output=True, check=True)

    def send_text(self, text):
        self._require("xclip")
        self._require("xdotool")
        # 用 xclip 写入剪贴板（支持 UTF-8），再 Ctrl+V
        self.set_clipboard(text)
        time.sleep(0.05)
        self.send_hotkey(["ctrl", "v"])

    def set_clipboard(self, text):
        """把文本写入剪贴板（xclip）；send_text / 探针读回式自动化共用"""
        self._require("xclip")
        subprocess.run(["xclip", "-selection", "clipboard"],
                       input=text.encode("utf-8"), check=True)

    def take_screenshot(self, output=None):
        if output is None:
            output = Path(tempfile.gettempdir()) / f"agentic_screenshot_{int(time.time() * 1000)}.png"
        else:
            output = Path(output)
        output.parent.mkdir(parents=True, exist_ok=True)

        # 依序尝试 gnome-screenshot / scrot / import(ImageMagick)
        for cmd, args in (
            (["gnome-screenshot", "-f", str(output)]),
            (["scrot", str(output)]),
            (["import", "-window", "root", str(output)]),
        ):
            if subprocess.run(["which", cmd[0]], capture_output=True).returncode == 0:
                subprocess.run(list(args), capture_output=True, check=True)
                return str(output.absolute())
        raise RuntimeError("缺少截图工具（gnome-screenshot / scrot / imagemagick 皆未安装）")

    def click_at(self, x, y):
        self._require("xdotool")
        subprocess.run(["xdotool", "mousemove", str(int(x)), str(int(y)), "click", "1"],
                       capture_output=True, check=True)
        return f"已点击 ({x}, {y})"

    def read_clipboard(self):
        """读取剪贴板文本（xclip -o）；为空 / 失败返回 "" """
        if subprocess.run(["which", "xclip"], capture_output=True).returncode != 0:
            return ""
        try:
            out = subprocess.run(["xclip", "-selection", "clipboard", "-o"],
                                 capture_output=True, timeout=3)
        except Exception:
            return ""
        if out.returncode != 0:
            return ""
        return out.stdout.decode("utf-8", errors="replace")

    def get_window_rect(self, hwnd):
        """获取窗口屏幕矩形（xdotool getwindowgeometry）"""
        self._require("xdotool")
        out = subprocess.run(
            ["xdotool", "getwindowgeometry", "--shell", str(int(hwnd))],
            capture_output=True, text=True, check=True,
        ).stdout
        values = {}
        for line in out.splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                values[k.strip()] = int(v.strip())
        x, y = values.get("X", 0), values.get("Y", 0)
        w, h = values.get("WIDTH", 0), values.get("HEIGHT", 0)
        return {"left": x, "top": y, "right": x + w, "bottom": y + h,
                "width": w, "height": h}


def get_controller() -> BaseAppController:
    """按当前平台返回应用控制器实例"""
    system = platform.system()
    if system == "Windows":
        return WindowsAppController()
    if system == "Linux":
        return LinuxAppController()
    raise RuntimeError(f"暂不支持平台: {system}")