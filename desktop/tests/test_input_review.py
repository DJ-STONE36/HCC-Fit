# -*- coding: utf-8 -*-
"""场景卡对话框的 offscreen 冒烟：单输入 → 字段 → 追问 → 确认门禁。"""
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


def main():
    app = QApplication([])
    d = InputReviewDialog(RAW, BASE / "resources" / "terms.csv")
    assert d.table.rowCount() >= 8
    assert not d.ok_btn.isEnabled(), "有必答追问时应禁用确认"
    assert d.session.fields["disease"].status == "ambiguous"
    assert "HCC" not in d.session.fields["disease"].normalized

    answers = {
        "q_disease": "HCC",
        "q_treatment": "已切除（是否根治不明）",
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
    d.close()
    print("PASS input review dialog: rows=%d, confirmed v%d" % (d.table.rowCount(), d.session.version))
    return 0


if __name__ == "__main__":
    sys.exit(main())
