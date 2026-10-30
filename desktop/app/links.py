# -*- coding: utf-8 -*-
"""打开外部链接：优先 Windows ShellExecute，其次 Qt，再 webbrowser。

为什么不用单一 QDesktopServices：在受限进程/无默认浏览器关联的环境里，
openUrl 可能返回 False 或“返回 True 但其实没打开”。这里逐个尝试，
失败时把链接复制到剪贴板并返回原因，界面必须给出可见反馈。
"""
from __future__ import annotations

import os
import webbrowser

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QApplication


def open_external_url(url: str) -> tuple[bool, str]:
    """返回 (是否成功, 方式或失败原因)。失败时已尝试复制链接到剪贴板。"""
    url = (url or "").strip()
    if not url:
        return False, "空链接"

    errors: list[str] = []

    # 1) Windows ShellExecute（最接近“双击链接”的行为）
    if os.name == "nt":
        try:
            os.startfile(url)  # type: ignore[attr-defined]
            return True, "startfile"
        except Exception as exc:  # noqa: BLE001
            errors.append("startfile: %s" % exc)

    # 2) Qt
    try:
        if QDesktopServices.openUrl(QUrl(url)):
            return True, "qt"
        errors.append("qt: 返回 False")
    except Exception as exc:  # noqa: BLE001
        errors.append("qt: %s" % exc)

    # 3) webbrowser
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
    return False, "；".join(errors[-3:])
