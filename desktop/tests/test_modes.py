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
    # 原始返回默认不占页签；勾选后才出现，且排在「补充资料」之后
    win.show_raw.setChecked(False)
    assert win.tabs.indexOf(win.raw_page) == -1, "默认不该显示原始返回页"
    win.show_raw.setChecked(True)
    assert win.tabs.indexOf(win.raw_page) >= 0
    assert win.tabs.tabText(win.tabs.indexOf(win.raw_page)) == "原始返回"
    assert (win.tabs.indexOf(win.raw_page)
            == win.tabs.indexOf(win.supplement_page) + 1), "原始返回应在补充资料之后"
    win.show_raw.setChecked(False)
    assert win.tabs.indexOf(win.raw_page) == -1
    assert win.mode.count() == 2, win.mode.count()
    win.select_mode("stub")           # 仅自动化：临时加入下拉框
    assert win.current_mode() == "stub"
    assert win.stage_list.count() == 5, win.stage_list.count()
    assert "离线自检" in win.badge.text(), win.badge.text()
    win.select_mode("sciencepal_hcc")
    assert win.stage_list.count() == 1, win.stage_list.count()
    win.close()
    return "选择/流程行随模式切换正常"


def case_names_example_highlight():
    """模式新名称、示例句、综合结论高亮。"""
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    win = MainWindow()
    labels = [win.mode.itemText(i) for i in range(win.mode.count())]
    assert labels == ["SciencePal HCC-Fit 多篇论文综合",
                      "SciencePal HCC-Fit 单篇论文核查"], labels
    assert win.scene_example.text().startswith(
        "示例：58 岁男性，乙肝，HCC，刚做完切除，病理还没出来"), win.scene_example.text()
    win._fill_scene_example()
    assert win.scene_input.toPlainText() == (
        "58 岁男性，乙肝，HCC，刚做完切除，病理还没出来，从今天开始，想看看三年总生存率")

    win.summary.setMarkdown("人群**匹配**；时点不匹配；输入无法确认")
    win._highlight_summary()
    doc = win.summary.document()
    assert doc.toPlainText() == "人群匹配；时点不匹配；输入无法确认", doc.toPlainText()
    bad = doc.find("不匹配")
    assert bad.charFormat().foreground().color().name() == "#c0392b", bad.charFormat().foreground().color().name()
    ok = doc.find("匹配")
    assert ok.charFormat().foreground().color().name() == "#1e8449", ok.charFormat().foreground().color().name()
    win.close()
    return "新名称/新示例/匹配绿-不匹配红"


def main():
    check("界面只剩 2 种大脑", case_ui_modes)
    check("人工/模拟实现已删", case_no_manual_brain)
    check("已移除模式明确报错", case_removed_mode_rejected)
    check("stub 仅测试可用", case_stub_still_works_for_tests)
    check("命名/示例/高亮", case_names_example_highlight)
    ok = sum(1 for r in RESULTS if r)
    print("\n%d/%d 通过" % (ok, len(RESULTS)))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
