# -*- coding: utf-8 -*-
"""大脑模式精简测试：界面只剩 2 种，已移除的 3 种不再可达。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "core"))

from app.main_window import MODES, MainWindow  # noqa: E402
from app.runner import build_planner  # noqa: E402
from app.export import MODE_LABEL  # noqa: E402

RESULTS = []


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append(True)
        print("PASS  %-38s %s" % (name, detail or ""))
    except Exception as exc:  # noqa: BLE001
        RESULTS.append(False)
        print("FAIL  %-38s %s: %s" % (name, type(exc).__name__, exc))


def case_ui_modes():
    assert [m for _, m in MODES] == ["sciencepal", "sciencepal_hcc"], MODES
    labels = [label for label, _ in MODES]
    for gone in ("演示", "本地模拟", "人工中转"):
        assert all(gone not in x for x in labels), labels
    return "下拉框：%s" % " / ".join(labels)


def case_no_manual_brain():
    import brains
    assert not hasattr(brains, "ManualBrain")
    assert not (BASE / "core" / "mock_sciencepal.py").exists()
    for gone in ("mock_sp", "manual"):
        assert gone not in MODE_LABEL, MODE_LABEL
    return "ManualBrain / mock_sciencepal 已删除"


def case_removed_mode_rejected():
    for gone in ("manual", "mock_sp"):
        try:
            build_planner(gone, str(BASE), {}, [], {}, lambda _e: None)
        except Exception as exc:  # noqa: BLE001
            assert "已移除" in str(exc), exc
        else:
            raise AssertionError("%s 不应再能构建 planner" % gone)
    return "manual / mock_sp → 明确报错"


def case_stub_still_works_for_tests():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    win = MainWindow()
    from PySide6.QtWidgets import QGroupBox
    titles = [g.title() for g in win.findChildren(QGroupBox)]
    assert any("SciencePal 连接" in t for t in titles), titles
    assert not any("大脑" in t for t in titles), titles
    assert hasattr(win, "split_papers"), "缺少逐篇发送复选框"
    assert "从今天开始" in win.scene_example.text(), win.scene_example.text()
    assert "总生存率" in win.scene_example.text(), win.scene_example.text()
    win._fill_scene_example()
    assert "从今天开始" in win.scene_input.toPlainText()
    assert win.mode.count() == 2, win.mode.count()
    win.select_mode("stub")           # 仅自动化：临时加入下拉框
    assert win.current_mode() == "stub"
    assert win.stage_list.count() == 5, win.stage_list.count()
    assert "离线自检" in win.badge.text(), win.badge.text()
    win.select_mode("sciencepal_hcc")
    assert win.stage_list.count() == 1, win.stage_list.count()
    win.close()
    return "选择/流程行随模式切换正常"


def main():
    check("界面只剩 2 种大脑", case_ui_modes)
    check("人工/模拟实现已删", case_no_manual_brain)
    check("已移除模式明确报错", case_removed_mode_rejected)
    check("stub 仅测试可用", case_stub_still_works_for_tests)
    ok = sum(1 for r in RESULTS if r)
    print("\n%d/%d 通过" % (ok, len(RESULTS)))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
