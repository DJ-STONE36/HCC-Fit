# -*- coding: utf-8 -*-
"""重新截取场景卡截图：保存到 desktop/docs/input_review.png。

注意：必须用真实平台（windows），offscreen 没有字体，文字会变成方框。
""" 
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "windows")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "desktop"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from app.input_review import InputReviewDialog  # noqa: E402

RAW = "肝癌患者，刚切完，病理还没出来，想看看三年生存。"


def main():
    app = QApplication([])
    dlg = InputReviewDialog(RAW, ROOT / "desktop" / "resources" / "terms.csv")
    dlg.resize(1080, 760)
    dlg.show()
    app.processEvents()
    out = ROOT / "desktop" / "docs" / "input_review.png"
    ok = dlg.grab().save(str(out))
    dlg.close()
    print("saved" if ok else "FAILED", out)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
