# -*- coding: utf-8 -*-
"""场景卡对话框的 offscreen 冒烟：单输入 → 字段 → 追问（只问缺的）→ 确认门禁。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402

from app.input_review import InputReviewDialog  # noqa: E402

RAW = "肝癌患者，刚切完，病理还没出来，想看看三年生存。"
FULL = "HCC，准备肝切除，从手术日预测三年复发"


def row_of(dlg, field_name):
    for i, (kind, key) in enumerate(dlg.row_fields):
        if kind == "field" and key == field_name:
            return i
    return -1


def main():
    app = QApplication([])

    # 1) 缺信息：疾病/起点/结局要问，治疗大类（刚切完）不问
    d = InputReviewDialog(RAW, BASE / "resources" / "terms.csv")
    assert d.table.rowCount() >= 8
    assert d.table.columnCount() == 7, d.table.columnCount()
    assert not d.ok_btn.isEnabled(), "有必答追问时应禁用确认"
    assert d.session.fields["disease"].status == "ambiguous"
    assert "HCC" not in d.session.fields["disease"].normalized
    ids = {f.id for f in d.session.followups}
    assert "q_disease" in ids and "q_start" in ids and "q_outcome" in ids, ids
    assert "q_treatment" not in ids, ids          # “刚切完”已说明治疗大类
    assert d.table.item(row_of(d, "treatment"), 6).text() == "足够"
    assert d.table.item(row_of(d, "disease"), 6).text() == "—"

    answers = {
        "q_disease": "HCC",
        "q_start": "手术日",
        "q_outcome": "总生存 OS",
    }
    for f in d.session.followups:
        if f.id in answers:
            combo, _ = d.follow_widgets[f.id]
            combo.setCurrentIndex(combo.findData(answers[f.id]))
            QTest.qWait(30)
    assert d.session.fields["disease"].normalized == "HCC"
    assert d.ok_btn.isEnabled(), d.hint.text()
    d.session.confirm()
    assert d.session.confirmation["status"] == "user_confirmed"
    assert d.session.version == 2
    rows = d.table.rowCount()
    d.close()

    # 2) 原话已足够：零追问，直接可确认
    full = InputReviewDialog(FULL, BASE / "resources" / "terms.csv")
    assert not full.session.followups, [f.id for f in full.session.followups]
    assert full.ok_btn.isEnabled(), full.hint.text()
    assert "不再追问" in full.hint.text(), full.hint.text()
    for name in ("disease", "treatment", "outcome", "start_point", "intent"):
        assert full.table.item(row_of(full, name), 6).text() == "足够", name
    full.close()

    # 3) 只缺“想预测多长时间”：回答 3 年后可直接确认
    no_h = InputReviewDialog("HCC，准备肝切除，从手术日起预测复发",
                             BASE / "resources" / "terms.csv")
    assert [f.id for f in no_h.session.followups] == ["q_horizon"], \
        [f.id for f in no_h.session.followups]
    combo, _ = no_h.follow_widgets["q_horizon"]
    combo.setCurrentIndex(combo.findData("3 年"))
    QTest.qWait(30)
    assert no_h.session.fields["horizon"].normalized == "36个月"
    assert no_h.ok_btn.isEnabled(), no_h.hint.text()
    no_h.close()

    print("PASS input review dialog: rows=%d, followups=%d, full=0, 7 列"
          % (rows, len(ids)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
