"""
utils.py
第4组：跨平台工具函数
提供编码安全输出、系统检测等跨平台兼容能力
"""

import sys
import platform
from pathlib import Path


def safe_print(text: str):
    """
    编码安全打印

    在 Windows 终端（GBK编码）上安全打印含中文/Unicode的文本，
    如果当前终端不支持某些字符，自动替换为安全版本。

    在 Ubuntu/Linux（UTF-8）上正常打印全部内容。
    """
    try:
        print(text)
    except UnicodeEncodeError:
        # 当前终端不支持这些字符，用ASCII近似替换
        safe_text = text.encode(
            sys.stdout.encoding or "ascii",
            errors="replace"
        ).decode(sys.stdout.encoding or "ascii")
        print(safe_text)


def get_downloads_path() -> Path:
    """
    获取当前系统的下载目录路径

    Returns:
        Windows: C:\\Users\\<用户名>\\Downloads
        Linux:   /home/<用户名>/Downloads
    """
    return Path.home() / "Downloads"


def get_temp_path() -> Path:
    """
    获取当前系统的临时目录路径

    Returns:
        Windows: C:\\Users\\<用户名>\\AppData\\Local\\Temp
        Linux:   /tmp
    """
    import os as _os
    return Path(_os.getenv("TEMP", _os.getenv("TMPDIR", "/tmp")))


def is_windows() -> bool:
    """判断当前是否为Windows系统"""
    return platform.system() == "Windows"


def is_linux() -> bool:
    """判断当前是否为Linux系统"""
    return platform.system() == "Linux"


def format_path(path: str) -> str:
    """将路径统一为当前系统的格式"""
    return str(Path(path))
