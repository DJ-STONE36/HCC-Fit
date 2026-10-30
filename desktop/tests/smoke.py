# -*- coding: utf-8 -*-
"""P0 冒烟测试：offscreen 起窗口 → stub 跑通 → 渲染 → 导出 JSON/PDF 并通过 schema。"""
import json
import os
import sys
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = Path(__file__).resolve().parent.parent
_DATA = BASE / "smoke_data" / uuid.uuid4().hex[:8]
_DATA.mkdir(parents=True, exist_ok=True)
os.environ["HCCFIT_DATA_DIR"] = str(_DATA)
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "core"))

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from app import export
from app.input_normalization import RuleExtractor
from app.main_window import MainWindow


def main():
    app = QApplication([])
    app.setOrganizationName("HCC-Fit")
    app.setApplicationName("HCC-Fit Desktop")

    win = MainWindow()
    win.mode.setCurrentIndex(0)  # stub
    raw = "58 岁男性，HBV 相关 HCC，8.2cm，Child-Pugh A，AFP 1210，准备肝切除，预测从手术日起一年复发"
    win.scene_input.setPlainText(raw)
    session = RuleExtractor(BASE / "resources" / "terms.csv").extract(raw)
    for f in session.followups:
        f.answer_status = "answered"
    session.confirm()
    win._apply_input_session(session)
    assert win.input_confirmed

    loop = QEventLoop()
    win.runFinished.connect(loop.quit)
    QTimer.singleShot(20000, loop.quit)
    win.start_run()
    loop.exec()
    QTest.qWait(300)  # 等 QThread 收尾

    if not win.visit:
        print("FAIL: 没有结果")
        print("error:", win.error.text())
        return 1

    v = win.visit
    print("PASS run:", v["mode"], "papers=%d matched=%d" % (len(v["papers"]), len(v["matched"])))
    assert v["mode"] == "stub"
    assert v["matched"], "没有匹配"
    assert win.matrix.rowCount() == len(v["matched"])
    assert win.matrix.columnCount() == 7
    assert win.matrix.item(0, 2).text() in ("强", "中", "弱", "不足")
    assert win.open_ws_btn.isEnabled()
    assert "完成" in win.stage_list.item(0).text() or "完成" in win.stage_list.item(1).text()

    # 四维/单次模式的流程行
    win.mode.setCurrentIndex(2)  # sciencepal_hcc
    assert win.stage_list.count() == 1, win.stage_list.count()
    win.mode.setCurrentIndex(4)  # manual
    assert not win.manual_hint.isHidden()
    win.mode.setCurrentIndex(0)
    assert win.stage_list.count() == 5

    # JSON + schema
    tmp = BASE / "smoke_out" / uuid.uuid4().hex[:8]
    tmp.mkdir(parents=True, exist_ok=True)
    jpath = tmp / "visit.json"
    export.export_json(v, jpath)
    from validate_output import validate
    schema = json.load(open(BASE / "resources" / "schemas" / "visit.schema.json", encoding="utf-8"))
    errs = validate(json.loads(jpath.read_text(encoding="utf-8")), schema)
    assert not errs, errs[:3]
    print("PASS json:", jpath.stat().st_size, "bytes, schema 0 错")

    # PDF（含 Markdown 结论）
    v2 = json.loads(json.dumps(v))
    v2["integrated"]["summary_zh"] = "**结论**\n\n- 人群匹配\n- 时点不匹配\n- 输入不可得"
    ppath = tmp / "report.pdf"
    export.export_pdf(v2, ppath)
    assert ppath.is_file() and ppath.stat().st_size > 1000, ppath.stat().st_size
    print("PASS pdf:", ppath.stat().st_size, "bytes")

    win._save_settings()
    win.close()
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
