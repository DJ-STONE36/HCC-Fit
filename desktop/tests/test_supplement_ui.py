# -*- coding: utf-8 -*-
"""补充资料 UI 冒烟：stub 报告 → 补充一段 → 全量重跑 → 新旧报告切换。"""
import json
import os
import shutil
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BASE = Path(__file__).resolve().parent.parent
_DATA = BASE / "tests" / "_tmp_supp_ui"
shutil.rmtree(_DATA, ignore_errors=True)
_DATA.mkdir(parents=True, exist_ok=True)
os.environ["HCCFIT_DATA_DIR"] = str(_DATA)
sys.path.insert(0, str(BASE))

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.input_normalization import RuleExtractor  # noqa: E402
from app.main_window import MainWindow  # noqa: E402


def main():
    app = QApplication([])
    app.setOrganizationName("HCC-Fit")
    app.setApplicationName("HCC-Fit Desktop")
    win = MainWindow()
    raw = "58 岁男性，HBV 相关 HCC，准备肝切除，预测从手术日起一年复发"
    win.scene_input.setPlainText(raw)
    session = RuleExtractor(BASE / "resources" / "terms.csv").extract(raw)
    for f in session.followups:
        f.answer_status = "answered"
    session.confirm()
    win._apply_input_session(session)
    win.mode.setCurrentIndex(0)
    win.show()
    QTest.qWait(200)

    win.start_run()
    for _ in range(400):
        QTest.qWait(50)
        if win.thread is None and win.visit:
            break
    assert win.visit, "第一次运行没有结果"
    old = win.visit
    assert old and old.get("expected_data"), "stub 应给出 expected_data"
    assert win.report_switch.isHidden()
    assert len(win.supplement_inputs) == len(old["expected_data"])
    assert "报告认为" not in win.supplement_expected.toPlainText()

    for _, edit in win.supplement_inputs:
        edit.setPlainText("病理：中分化 HCC，MVI 阴性；手术日期 2026-10-20。")
    QTest.qWait(100)
    assert win.supplement_btn.isEnabled()

    original_q = QMessageBox.question
    QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
    try:
        win.start_supplement_run()
        for _ in range(400):
            QTest.qWait(50)
            if win.thread is None and win.visit is not old:
                break
    finally:
        QMessageBox.question = original_q
    new = win.visit
    if new is old:
        print("DEBUG supplement not started:",
              "thread=", win.thread,
              "error=", win.error.text(),
              "status=", win.statusBar().currentMessage(),
              "supp_status=", win.supplement_status.text())
    assert new is not old, "补充运行没有产生新报告"
    assert new and new["visit_id"] != old["visit_id"]
    assert win.previous_visit["visit_id"] == old["visit_id"]
    assert "报告后补充资料" in new["scenario"]["patient_summary_zh"]
    given = new["scenario"]["inputs_given"]
    assert "报告后补充：" in ("、".join(given) if isinstance(given, list) else given)
    assert win.report_switch.count() == 2
    assert not win.report_switch.isHidden()
    assert all(not edit.toPlainText() for _, edit in win.supplement_inputs)

    sups = list((_DATA / "supplements").glob("sup_*.json"))
    assert sups, "补充记录未落盘"
    rec = json.loads(sups[0].read_text(encoding="utf-8"))
    assert rec["applied"] is True
    assert rec["new_visit_id"] == new["visit_id"]

    win.report_switch.setCurrentIndex(1)
    QTest.qWait(100)
    assert win.visit["visit_id"] == old["visit_id"]
    win.report_switch.setCurrentIndex(0)
    QTest.qWait(100)
    assert win.visit["visit_id"] == new["visit_id"]

    win.close()
    shutil.rmtree(_DATA, ignore_errors=True)
    print("PASS supplement UI: old=%s new=%s" % (old["visit_id"], new["visit_id"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
