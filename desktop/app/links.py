# -*- coding: utf-8 -*-
"""打开外部链接：优先用注册表里的默认浏览器直接启动，其次 ShellExecute/Qt/webbrowser。

为什么不用单一 os.startfile：它“成功返回”不代表浏览器真的打开了
（默认浏览器被替换、进程受限、URL 关联丢失时都会静默失败）。
这里优先读 HKCU\\...\\UrlAssociations 的 ProgId，拿到真实浏览器命令行再启动。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import os
import subprocess
import time
import webbrowser

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication

_USER_CHOICE = (r"Software\Microsoft\Windows\Shell\Associations"
                r"\UrlAssociations\http\UserChoice")


def _read_reg(root, path: str, name: str = "") -> str:
    try:
        import winreg
        with winreg.OpenKey(root, path) as key:
            return str(winreg.QueryValueEx(key, name)[0])
    except Exception:  # noqa: BLE001
        return ""


def default_browser_command() -> str:
    """返回默认浏览器的 open command（含 %1 占位），拿不到返回 ""。"""
    if os.name != "nt":
        return ""
    import winreg

    prog = _read_reg(winreg.HKEY_CURRENT_USER, _USER_CHOICE, "ProgId")
    if prog:
        cmd = _read_reg(winreg.HKEY_CLASSES_ROOT, prog + r"\shell\open\command")
        if cmd:
            return cmd
    for path in (r"https\shell\open\command", r"http\shell\open\command"):
        cmd = _read_reg(winreg.HKEY_CLASSES_ROOT, path)
        if cmd:
            return cmd
    return ""


def build_cmdline(command: str, url: str) -> str:
    """把命令模板里的 %1 换成带引号的 URL（模板本身已带引号则不重复加）。"""
    if '"%1"' in command:
        return command.replace('"%1"', '"%s"' % url)
    if "%1" in command:
        return command.replace("%1", '"%s"' % url)
    return '%s "%s"' % (command, url)


EXIT_PREFIX = "浏览器进程启动后立即退出"


def _browser_window_count() -> int:
    """统计可见的浏览器窗口（Edge/Chrome），不需要进程权限、也不走管道。"""
    if os.name != "nt":
        return 0
    try:
        user32 = ctypes.windll.user32
        count = 0

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def _cb(hwnd, _lparam):
            nonlocal count
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value in ("Chrome_WidgetWin_1", "MozillaWindowClass"):
                count += 1
            return True

        user32.EnumWindows(_cb, 0)
        return count
    except Exception:  # noqa: BLE001
        return -1


def _wait_browser_window(before: int, timeout: float = 3.0) -> bool:
    """等浏览器窗口出现（ShellExecute 是异步的）。"""
    if before < 0:
        return False
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _browser_window_count() > before:
            return True
        time.sleep(0.25)
    return False


def _launch_explorer(url: str) -> tuple[bool, str]:
    if os.name != "nt":
        return False, "explorer: 仅 Windows"
    before = _browser_window_count()
    try:
        subprocess.Popen(["explorer.exe", url], close_fds=True)
    except Exception as exc:  # noqa: BLE001
        return False, "explorer: %s" % exc
    if _wait_browser_window(before, 2.5):
        return True, "explorer"
    return False, "explorer: 未出现浏览器窗口"


def _launch_default_browser(url: str) -> tuple[bool, str]:
    """启动默认浏览器；等 1.2s 看它是否立刻退出（被安全软件/沙箱拦截时 exit≠0）。"""
    cmd = default_browser_command()
    if not cmd:
        return False, "没读到默认浏览器"
    try:
        proc = subprocess.Popen(build_cmdline(cmd, url), close_fds=True)
    except Exception as exc:  # noqa: BLE001
        return False, "default-browser: %s" % exc
    try:
        code = proc.wait(timeout=1.2)
    except subprocess.TimeoutExpired:
        return True, "default-browser"       # 还活着：窗口已起来
    if code == 0:
        return True, "default-browser(handoff)"   # 交给已有浏览器实例后正常退出
    return False, "%s（exit=%d）" % (EXIT_PREFIX, code)


def open_external_url(url: str) -> tuple[bool, str]:
    """返回 (是否成功, 方式或失败原因)。失败时会把链接复制到剪贴板。"""
    url = (url or "").strip()
    if not url:
        return False, "空链接"

    errors: list[str] = []

    # 0) 没有浏览器窗口时，先走官方 ShellExecute（用户环境最稳），并用窗口数验证
    before = _browser_window_count()
    if before == 0:
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            if _wait_browser_window(before, 3.0):
                return True, "startfile"
            errors.append("startfile: 浏览器窗口未出现")
        except Exception as exc:  # noqa: BLE001
            errors.append("startfile: %s" % exc)

    # 2) 真实默认浏览器（最可靠）
    ok, how = _launch_default_browser(url)
    if ok:
        return True, how
    errors.append(how)
    if how.startswith(EXIT_PREFIX):
        # 浏览器进程启动即被拦截：ShellExecute/Qt 用的是同一个处理器，不再假装成功
        try:
            QApplication.clipboard().setText(url)
        except Exception:  # noqa: BLE001
            pass
        return False, how + "；可能被安全软件/运行环境限制，已复制链接，请手动粘贴到浏览器"

    # 2) Windows ShellExecute
    if os.name == "nt":
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return True, "startfile"
        except Exception as exc:  # noqa: BLE001
            errors.append("startfile: %s" % exc)

    # 2.5) explorer 走一遍 shell（极少数环境 ShellExecute 不生效）
    ok, how = _launch_explorer(url)
    if ok:
        return True, how
    errors.append(how)

    # 3) Qt
    try:
        if QDesktopServices.openUrl(QUrl(url)):
            return True, "qt"
        errors.append("qt: 返回 False")
    except Exception as exc:  # noqa: BLE001
        errors.append("qt: %s" % exc)

    # 4) webbrowser
    try:
        if webbrowser.open(url):
            return True, "webbrowser"
        errors.append("webbrowser: 返回 False")
    except Exception as exc:  # noqa: BLE001
        errors.append("webbrowser: %s" % exc)

    try:
        QApplication.clipboard().setText(url)
    except Exception:  # noqa: BLE001
        pass
    return False, "；".join(errors[-4:])
