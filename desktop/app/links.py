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
import re
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


def browser_exe() -> str:
    """默认浏览器 exe 路径（直接传 URL，比 --single-argument 稳）。"""
    cmd = default_browser_command()
    if not cmd:
        return ""
    m = re.match(r'\s*"([^"]+)"', cmd)
    if m:
        return m.group(1)
    m = re.match(r"\s*(\S+)", cmd)
    return m.group(1) if m else ""


def build_cmdline(command: str, url: str) -> str:
    """把命令模板里的 %1 换成带引号的 URL（模板本身已带引号则不重复加）。"""
    if '"%1"' in command:
        return command.replace('"%1"', '"%s"' % url)
    if "%1" in command:
        return command.replace("%1", '"%s"' % url)
    return '%s "%s"' % (command, url)


EXIT_PREFIX = "浏览器进程启动后立即退出"


def _window_is_edge(hwnd) -> bool:
    """只认 msedge.exe 的窗口（WebView2/Electron 也用 Chrome_WidgetWin_1）。"""
    try:
        pid = ctypes.wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return False
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            buf = ctypes.create_unicode_buffer(1024)
            size = ctypes.wintypes.DWORD(1024)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return False
            return buf.value.lower().endswith("msedge.exe")
        finally:
            kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        return False


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
            if buf.value in ("Chrome_WidgetWin_1", "MozillaWindowClass") and _window_is_edge(hwnd):
                count += 1
            return True

        user32.EnumWindows(_cb, 0)
        return count
    except Exception:  # noqa: BLE001
        return -1


def _focus_browser_window() -> bool:
    """把浏览器窗口提到前台（最小化的先还原）。"""
    if os.name != "nt":
        return False
    try:
        user32 = ctypes.windll.user32
        hwnds = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def _cb(hwnd, _lparam):
            if not user32.IsWindowVisible(hwnd):
                return True
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            if buf.value in ("Chrome_WidgetWin_1", "MozillaWindowClass") \
                    and user32.GetWindowTextLengthW(hwnd) > 0 and _window_is_edge(hwnd):
                hwnds.append(hwnd)
            return True

        user32.EnumWindows(_cb, 0)
        if not hwnds:
            return False
        hwnd = hwnds[-1]
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)      # SW_RESTORE
        user32.SetForegroundWindow(hwnd)
        return True
    except Exception:  # noqa: BLE001
        return False


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
    """直接启动默认浏览器 exe + URL（不依赖 URL 关联），看 2s 退出码。"""
    exe = browser_exe()
    if not exe:
        return False, "没读到默认浏览器"
    try:
        proc = subprocess.Popen([exe, url], close_fds=True)
    except Exception as exc:  # noqa: BLE001
        return False, "browser: %s" % exc
    try:
        code = proc.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        return True, "browser"           # 还活着：窗口已起来
    if code == 0:
        return True, "browser(handoff)"  # 交给已有实例后正常退出
    return False, "%s（exit=%d）" % (EXIT_PREFIX, code)


def _log_attempt(url: str, ok: bool, how: str) -> None:
    try:
        from app import paths  # 延迟导入，避免循环
        d = paths.app_data_dir() / "logs"
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "links.log", "a", encoding="utf-8") as fh:
            fh.write("%s\t%s\t%s\t%s\n" % (
                time.strftime("%Y-%m-%d %H:%M:%S"), "OK" if ok else "FAIL", how, url))
    except Exception:  # noqa: BLE001
        pass


def open_external_url(url: str) -> tuple[bool, str]:
    ok, how = _open_external_url(url)
    _log_attempt(url, ok, how)
    return ok, how


def _open_external_url(url: str) -> tuple[bool, str]:
    """返回 (是否成功, 方式或失败原因)。失败时会把链接复制到剪贴板。"""
    url = (url or "").strip()
    if not url:
        return False, "空链接"

    errors: list[str] = []

    # 1) 直接启动默认浏览器 exe + URL（新窗口/新标签页都能走，不依赖 URL 关联）
    ok, how = _launch_default_browser(url)
    if ok:
        _focus_browser_window()
        return True, how
    errors.append(how)

    # 2) ShellExecute 兜底（URL 关联正常时最省事）
    before = _browser_window_count()
    try:
        os.startfile(url)  # type: ignore[attr-defined]
        if before > 0:
            _focus_browser_window()
            return True, "startfile(existing)"
        if _wait_browser_window(before, 4.0):
            _focus_browser_window()
            return True, "startfile"
        errors.append("startfile: 浏览器窗口未出现")
    except Exception as exc:  # noqa: BLE001
        errors.append("startfile: %s" % exc)

    # 3) explorer 走一遍 shell
    ok, how = _launch_explorer(url)
    if ok:
        return True, how
    errors.append(how)

    # 4) Qt
    try:
        if QDesktopServices.openUrl(QUrl(url)):
            return True, "qt"
        errors.append("qt: 返回 False")
    except Exception as exc:  # noqa: BLE001
        errors.append("qt: %s" % exc)

    # 5) webbrowser
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
    summary = "；".join(errors[-4:])
    if errors and errors[0].startswith(EXIT_PREFIX) and EXIT_PREFIX not in summary:
        summary = errors[0] + "；" + summary
    return False, summary
