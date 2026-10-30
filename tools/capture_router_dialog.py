# -*- coding: utf-8 -*-
"""重新截取“找候选论文”对话框（内置 Router 版），保存到 desktop/docs/router_dialog.png。

注意：必须用真实平台（windows），offscreen 没有字体。
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.router_dialog import RouterDialog  # noqa: E402

SCENARIO = {
    "text": "58 岁男性，HBV 相关 HCC，准备肝切除，预测从手术日起一年复发",
    "disease": "HCC",
    "time_zero": "surgery",
    "treatment_state": "curative resection",
    "outcome": "RFS",
    "horizon_months": 12,
}


SAMPLE_PAPERS = [
    {"pmid": "37850181", "year": 2023, "journal": "Eur Radiol",
     "title": "Deep Segmentation Feature-Based Radiomics Improves Recurrence Prediction "
              "in Early-Stage Hepatocellular Carcinoma After Curative Resection: "
              "A Multicenter Retrospective Study",
     "readability": {"level": "abstract_only"}, "why": ["结局/时点匹配"],
     "risk": ["回顾性、单中心为主"]},
    {"pmid": "35296018", "year": 2021, "journal": "Ann Surg Oncol",
     "title": "Nomograms for Predicting Hepatocellular Carcinoma Recurrence and Overall "
              "Survival After Liver Transplantation: External Validation",
     "readability": {"level": "fulltext"}, "why": ["有外部验证"],
     "risk": ["人群为移植，和切除人群不同"]},
]


def main():
    app = QApplication([])
    dlg = RouterDialog(SCENARIO, "sp_live_demo_abcdefghijklmnop")
    dlg._on_done({"papers": SAMPLE_PAPERS, "warnings": [],
                  "strategy_used": "fast"})   # 截图展示长标题换行 + 勾选态
    dlg.resize(1040, 700)
    dlg.show()
    app.processEvents()
    out = ROOT / "desktop" / "docs" / "router_dialog.png"
    ok = dlg.grab().save(str(out))
    dlg.close()
    print("saved" if ok else "FAILED", out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
