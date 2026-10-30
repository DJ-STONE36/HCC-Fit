# -*- coding: utf-8 -*-
"""截取主窗口（左栏：1 大脑 / 2 场景 / 3 论文），保存到 desktop/docs/main_window.png。

注意：必须用真实平台（windows），offscreen 没有字体。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.main_window import MainWindow  # noqa: E402


def main():
    app = QApplication([])
    app.setOrganizationName("HCC-Fit")
    app.setApplicationName("HCC-Fit Desktop")
    win = MainWindow()
    win.key.setText("sp_live_demo_abcdefghijklmnop")   # 只用于截图，不落盘
    win.scene_input.setPlainText(
        "58 岁男性，乙肝，肝癌，刚做完切除，病理还没出来，从今天开始，想看看三年总生存率；如果做模型核查，请说明是否 HCC、切除是否根治。")
    win.resize(1420, 900)
    win.show()
    app.processEvents()
    out = ROOT / "desktop" / "docs" / "main_window.png"
    ok = win.grab().save(str(out))
    win.close()
    print("saved" if ok else "FAILED", out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
