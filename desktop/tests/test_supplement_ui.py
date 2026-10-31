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
sys.path.insert(0, str(BASE / "tests"))

from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from app.input_normalization import RuleExtractor  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.supplement import with_doc_texts  # noqa: E402
from pdf_fixture import PATHOLOGY, write_text_pdf  # noqa: E402


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
    win.select_mode("stub")
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

    # 「原始返回」勾选后应排在「补充资料」之后（最后一个页签）
    win.show_raw.setChecked(True)
    assert win.tabs.indexOf(win.raw_page) == win.tabs.indexOf(win.supplement_page) + 1, (
        "原始返回页签应在补充资料之后")
    win.show_raw.setChecked(False)
    assert win.tabs.indexOf(win.raw_page) == -1

    # 补充资料阶段可上传 PDF：原样作为附件发给 Agent（不抽文本）
    pdf = write_text_pdf(_DATA / "病理报告.pdf", PATHOLOGY)
    win.add_supplement_pdfs([str(pdf), str(pdf)])          # 重复加入应去重
    assert len(win.supplement_files) == 1, win.supplement_files
    assert set(win.supplement_files[0]) == {"path", "name"}, win.supplement_files[0]
    assert win.supplement_files[0]["name"] == "病理报告.pdf"
    assert "病理报告.pdf" not in win._supplement_text(), "附件不再抽文本进补充原文"
    assert not win.supplement_files_list.isHidden(), "有附件时应显示附件列表"

    # 只有附件、没填文字时也应允许重新提交
    for _, edit in win.supplement_inputs:
        edit.setPlainText("")
    QTest.qWait(100)
    assert win.supplement_btn.isEnabled(), win.supplement_status.text()

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
    assert "另附资料文件（请直接读取附件，不要当论文）：病理报告.pdf" in new["scenario"]["patient_summary_zh"]
    assert "MVI 阴性" in new["scenario"]["patient_summary_zh"]
    assert new["provenance"]["attachments"] == ["病理报告.pdf"], new["provenance"]
    assert (Path(win.workspace) / "files" / "病理报告.pdf").is_file(), "附件应落盘到工作区"
    assert not win.supplement_files, "新一轮报告应清空上一轮附件"
    given = new["scenario"]["inputs_given"]
    assert "报告后补充：" in ("、".join(given) if isinstance(given, list) else given)
    assert win.report_switch.count() == 2
    assert not win.report_switch.isHidden()
    assert [win.report_switch.itemText(i) for i in range(2)] == ["第 2 次报告", "第 1 次报告"]
    assert win.report_switch.itemData(0).get("visit_id") == new["visit_id"]
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

    # 续会话判定：有 thread_id 且未勾“新开会话”→ 沿用原会话，附件转文字
    win.visit["provenance"]["thread_id"] = "th-test"
    win.visit["provenance"]["workspace"] = str(win.workspace)
    assert win._supplement_plan()[0] is True
    win.add_supplement_pdfs([str(pdf)])
    docs, bad = win._supplement_doc_texts()
    assert docs and docs[0]["text"] and not bad, (docs, bad)
    assert "【附件《病理报告.pdf》的文字内容" in with_doc_texts("补充", docs)
    assert not win.supplement_new_session.isHidden(), "有附件时应出现“新开会话”复选框"
    win.supplement_new_session.setChecked(True)
    assert win._supplement_plan()[0] is False, "勾选后应改为新开会话"
    win.supplement_new_session.setChecked(False)
    assert win._supplement_plan()[0] is True
    win.clear_supplement_files()

    # 历次报告：继续累积，标签按 第 N 次报告 递减
    for i in range(2):
        win._history_add({"visit_id": "fake-%d" % i, "created_at": "2026-10-31T18:00:00",
                          "mode": "sciencepal"})
    assert win.report_switch.count() == 4, win.report_switch.count()
    assert [win.report_switch.itemText(i) for i in range(4)] == [
        "第 4 次报告", "第 3 次报告", "第 2 次报告", "第 1 次报告"]
    assert win.report_switch.currentIndex() == 0, "新增报告后应选中最新一份"
    assert win.report_history[0]["visit_id"] == "fake-1"
    assert win.previous_visit["visit_id"] == "fake-0"
    assert win.report_history[-1]["visit_id"] == old["visit_id"]

    win.close()
    shutil.rmtree(_DATA, ignore_errors=True)
    print("PASS supplement UI: old=%s new=%s" % (old["visit_id"], new["visit_id"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
