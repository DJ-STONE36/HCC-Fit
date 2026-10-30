# -*- coding: utf-8 -*-
"""HCC-Fit 桌面版入口（PySide6）。"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))
if str(BASE / "core") not in sys.path:
    sys.path.insert(0, str(BASE / "core"))

from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication

from app.export import cjk_font_family
from app.main_window import MainWindow


def _make_app():
    app = QApplication([sys.argv[0]])
    app.setOrganizationName("HCC-Fit")
    app.setApplicationName("HCC-Fit Desktop")
    app.setApplicationDisplayName("HCC-Fit")
    app.setFont(QFont(cjk_font_family(), 10))
    return app


def _smoke() -> int:
    """打包后的自检：不显示窗口，跑一次 stub，成功返回 0。"""
    from PySide6.QtCore import QEventLoop, QTimer

    app = _make_app()
    win = MainWindow()
    win.mode.setCurrentIndex(0)  # stub
    from app.input_normalization import RuleExtractor
    raw = ("58 岁男性，HBV 相关 HCC，8.2cm，Child-Pugh A，AFP 1210，"
           "准备肝切除，预测从手术日起一年复发")
    win.scene_input.setPlainText(raw)
    session = RuleExtractor(BASE / "resources" / "terms.csv").extract(raw)
    for f in session.followups:
        f.answer_status = "answered"
    session.confirm()
    win._apply_input_session(session)
    loop = QEventLoop()
    win.runFinished.connect(loop.quit)
    QTimer.singleShot(30000, loop.quit)
    win.start_run()
    loop.exec()
    ok = bool(win.visit and win.visit.get("matched"))
    if ok:
        try:
            from app import export, paths
            out = paths.app_data_dir() / "smoke_report.pdf"
            export.export_pdf(win.visit, out)
            ok = out.is_file() and out.stat().st_size > 1000
            from app.input_review import InputReviewDialog
            dlg = InputReviewDialog(
                "肝癌患者，刚切完，病理还没出来，想看看三年生存。",
                paths.resource("terms.csv"))
            ok = ok and dlg.table.rowCount() >= 8
            dlg.close()
        except Exception:
            ok = False
    try:
        print("SMOKE", "OK" if ok else "FAIL", (win.visit or {}).get("visit_id", ""))
    except Exception:
        pass
    return 0 if ok else 2


def main():
    if "--smoke" in sys.argv:
        return _smoke()
    app = _make_app()
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
