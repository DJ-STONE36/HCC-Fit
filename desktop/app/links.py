# -*- coding: utf-8 -*-
"""打开外部链接：优先用注册表里的默认浏览器直接启动，其次 ShellExecute/Qt/webbrowser。

为什么不用单一 os.startfile：它“成功返回”不代表浏览器真的打开了
（默认浏览器被替换、进程受限、URL 关联丢失时都会静默失败）。
这里优先读 HKCU\\...\\UrlAssociations 的 ProgId，拿到真实浏览器命令行再启动。
"""
from __future__ import annotations

import os
import subprocess
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

    # 1) 真实默认浏览器（最可靠）
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
