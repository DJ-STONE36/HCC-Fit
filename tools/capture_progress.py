# -*- coding: utf-8 -*-
"""截取“流程”面板的逐篇进度行，保存到 desktop/docs/per_paper_progress.png。"""
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
    win.select_mode("sciencepal")
    win._rebuild_stages()
    for ev in (
        {"stage": "domain", "label": "S0 域判定", "status": "ok", "ms": 8120, "detail": ""},
        {"stage": "papers", "label": "S1 论文选择", "status": "ok", "ms": 15400, "detail": "纳入 3 / 共 3 篇"},
        {"stage": "match", "label": "S2 逐篇匹配", "status": "start"},
        {"stage": "match#0", "label": "S2 逐篇匹配 · 1/3 a.pdf", "status": "ok",
         "ms": 18200, "detail": "1 篇完成匹配，1 篇可讨论"},
        {"stage": "match#1", "label": "S2 逐篇匹配 · 2/3 b.pdf", "status": "start"},
    ):
        win._on_stage(ev)
    win.resize(1420, 900)
    win.show()
    app.processEvents()
    out = ROOT / "desktop" / "docs" / "per_paper_progress.png"
    ok = win.grab().save(str(out))
    win.close()
    print("saved" if ok else "FAILED", out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
