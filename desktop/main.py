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
    """打包后的自检：不显示窗口，跑一次 stub，成功返回 0。

    失败时把原因写到 `AppLocalData/smoke.json` 并打印出来，便于无控制台的 exe 排错。
    """
    import json
    import traceback

    from PySide6.QtCore import QEventLoop, QTimer

    detail = {}
    app = _make_app()
    win = MainWindow()
    win.select_mode("stub")  # 离线自检：界面不提供，但 runner 保留
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
    detail["visit"] = bool(win.visit)
    detail["matched"] = ok
    # 内置 Router 必须随包可用（打包后最容易退化的部分）
    try:
        from app.router_engine import engine_status
        st = engine_status()
        detail["router"] = st
        ok = ok and bool(st.get("available"))
    except Exception as exc:  # noqa: BLE001
        detail["router_error"] = "%s: %s" % (type(exc).__name__, exc)
        detail["router_traceback"] = traceback.format_exc()[-2000:]
        ok = False
    if ok:
        try:
            from app import export, paths
            out = paths.app_data_dir() / "smoke_report.pdf"
            export.export_pdf(win.visit, out)
            ok = out.is_file() and out.stat().st_size > 1000
            detail["pdf"] = ok
            from app.input_review import InputReviewDialog
            dlg = InputReviewDialog(
                "肝癌患者，刚切完，病理还没出来，想看看三年生存。",
                paths.resource("terms.csv"))
            detail["dialog_rows"] = dlg.table.rowCount()
            ok = ok and dlg.table.rowCount() >= 8
            dlg.close()
        except Exception as exc:  # noqa: BLE001
            detail["render_error"] = "%s: %s" % (type(exc).__name__, exc)
            ok = False
    detail["ok"] = ok
    try:
        from app import paths
        (paths.app_data_dir() / "smoke.json").write_text(
            json.dumps(detail, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    try:
        print("SMOKE", "OK" if ok else "FAIL", (win.visit or {}).get("visit_id", ""),
              json.dumps(detail, ensure_ascii=False))
    except Exception:
        pass
    return 0 if ok else 2


def _router_smoke() -> int:
    """只测内置 Router（会联网访问 PubMed）：`HCC-Fit.exe --router-smoke`。

    退出码：0 正常；2 引擎不可用；3 检索失败。
    """
    import json

    from app.router_engine import engine_status, search

    st = engine_status()
    print("ENGINE", json.dumps({k: st.get(k) for k in
                                ("available", "path", "strategies", "error")},
                               ensure_ascii=False))
    if not st.get("available"):
        return 2
    scenario = {"text": "HCC 切除后想预测 1 年复发", "disease": "HCC",
                "time_zero": "surgery", "outcome": "RFS", "horizon_months": 12}
    try:
        data = search(scenario, strategy="fast", k=3, deadline_ms=60000)
    except Exception as exc:  # noqa: BLE001
        print("SEARCH FAIL", "%s: %s" % (type(exc).__name__, exc))
        return 3
    papers = data.get("papers") or []
    print("SEARCH OK papers=%d strategy=%s" % (len(papers), data.get("strategy_used")))
    for p in papers[:3]:
        print("  -", p.get("pmid"), str(p.get("title") or "")[:70])
    return 0


def _open_url(url: str) -> int:
    """排错用：HCC-Fit.exe --open-url <URL>，结果写 logs/links.log。"""
    from app import links
    app = _make_app()
    ok, how = links.open_external_url(url)
    print("OPEN", "OK" if ok else "FAIL", how)
    from PySide6.QtWidgets import QMessageBox
    QMessageBox.information(
        None, "打开链接测试",
        "结果：%s\n方式/原因：%s\n\n链接：%s"
        % ("成功" if ok else "失败", how, url))
    return 0 if ok else 2


def main():
    if "--open-url" in sys.argv:
        idx = sys.argv.index("--open-url")
        url = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        return _open_url(url)
    if "--smoke" in sys.argv:
        return _smoke()
    if "--router-smoke" in sys.argv:
        return _router_smoke()
    app = _make_app()
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
