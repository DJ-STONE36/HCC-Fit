# -*- coding: utf-8 -*-
"""多篇论文逐篇发送（split_papers）测试。"""
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "core"))

from planner import HccPlanner, Planner, _merge_hcc_visits  # noqa: E402

RESULTS = []


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append(True)
        print("PASS  %-42s %s" % (name, detail or ""))
    except Exception as exc:  # noqa: BLE001
        RESULTS.append(False)
        print("FAIL  %-42s %s: %s" % (name, type(exc).__name__, exc))


class FakeBrain:
    name = "fake"

    def __init__(self):
        self.calls = []

    def run_stage(self, stage, payload):
        self.calls.append((stage, list(payload.get("files") or []),
                           len(payload.get("included") or [])))
        if stage == "domain":
            return {"status": "in_scope", "note_zh": ""}
        if stage == "papers":
            return {"papers": [
                {"paper_id": "p1", "title": "A", "decision": "include", "file_path": "a.pdf"},
                {"paper_id": "p2", "title": "B", "decision": "include", "file_path": "b.pdf"},
            ]}
        if stage == "match":
            return {"matched": [{"paper_id": payload["included"][0]["paper_id"],
                                 "dimensions": [], "tier": "applicable",
                                 "evidence_strength": "moderate"}]}
        if stage == "integrate":
            return {"summary_zh": "s", "options": [], "cautions_zh": []}
        return {"expected_data": []}


def _ws():
    ws = BASE / "tests" / "_tmp_split"
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def case_s2_per_paper():
    ws = _ws()
    brain = FakeBrain()
    planner = Planner(brain, str(ws), {"patient_summary_zh": "x", "task_zh": "y"},
                      files=["a.pdf", "b.pdf"], split_papers=True)
    visit = planner.run()
    matches = [c for c in brain.calls if c[0] == "match"]
    assert len(matches) == 2, matches
    assert [m[1] for m in matches] == [["a.pdf"], ["b.pdf"]], matches
    assert len(visit["matched"]) == 2, visit["matched"]
    shutil.rmtree(ws, ignore_errors=True)
    return "S2 每篇一次调用，files 各 1 个"


def case_s2_together_by_default():
    ws = _ws()
    brain = FakeBrain()
    planner = Planner(brain, str(ws), {"patient_summary_zh": "x", "task_zh": "y"},
                      files=["a.pdf", "b.pdf"], split_papers=False)
    planner.run()
    matches = [c for c in brain.calls if c[0] == "match"]
    assert len(matches) == 1, matches
    assert matches[0][2] == 2, matches          # 一次带 2 篇
    shutil.rmtree(ws, ignore_errors=True)
    return "默认仍是一次带全部"


def _fake_visit(name):
    return {
        "papers": [{"paper_id": name, "title": name, "file_path": name}],
        "matched": [{"paper_id": name, "tier": "applicable", "dimensions": [],
                     "evidence_strength": "moderate"}],
        "integrated": {"summary_zh": "摘要-" + name, "options": [], "cautions_zh": ["c1"]},
        "expected_data": [{"item_zh": "MVI", "kind": "pathology"}],
        "uncertainties": ["u1"],
        "raw_answers": [{"stage": "hcc", "text": name}],
        "raw_excerpts": [],
        "parse_mode": "structured",
        "provenance": {"files": [name], "stages_ms": {"hcc": 10}},
    }


def case_hcc_per_paper_merge():
    ws = _ws()
    planner = HccPlanner(FakeBrain(), str(ws), {"patient_summary_zh": "x"},
                         files=["a.pdf", "b.pdf"], split_papers=True)
    calls = []

    def fake_one(one):
        calls.append(one)
        return _fake_visit(one[0])

    planner._run_one = fake_one
    planner._save = lambda _v: None
    visit = planner.run()
    assert calls == [["a.pdf"], ["b.pdf"]], calls
    assert len(visit["papers"]) == 2 and len(visit["matched"]) == 2
    assert "摘要-a.pdf" in visit["integrated"]["summary_zh"]
    assert "摘要-b.pdf" in visit["integrated"]["summary_zh"]
    assert visit["expected_data"] == [{"item_zh": "MVI", "kind": "pathology"}]   # 去重
    assert visit["provenance"]["stages_ms"]["hcc"] == 20
    shutil.rmtree(ws, ignore_errors=True)
    return "逐篇核查后合并 papers/matched/期望数据"


def case_merge_helpers():
    merged = _merge_hcc_visits([_fake_visit("a"), _fake_visit("b")])
    assert merged["parse_mode"] == "structured"
    assert len(merged["matched"]) == 2
    assert len(merged["integrated"]["cautions_zh"]) == 1    # c1 去重
    return "合并去重正确"



def case_planner_emits_child_keys():
    ws = _ws()
    brain = FakeBrain()
    planner = Planner(brain, str(ws), {"patient_summary_zh": "x", "task_zh": "y"},
                      files=["a.pdf", "b.pdf"], split_papers=True)
    events = []
    planner.emit = events.append
    planner.run()
    keys = [e.get("stage") for e in events]
    assert "match#0" in keys and "match#1" in keys, keys
    labels = {e["stage"]: e.get("label") for e in events if e.get("stage", "").startswith("match#")}
    assert "1/2" in labels["match#0"] and "2/2" in labels["match#1"], labels
    shutil.rmtree(ws, ignore_errors=True)
    return "S2 逐篇事件带编号与文件名"


def case_hcc_emits_child_keys():
    ws = _ws()
    planner = HccPlanner(FakeBrain(), str(ws), {"patient_summary_zh": "x"},
                         files=["a.pdf", "b.pdf"], split_papers=True)
    planner._run_one = lambda one: _fake_visit(one[0])
    planner._save = lambda _v: None
    events = []
    planner.emit = events.append
    planner.run()
    keys = [e.get("stage") for e in events]
    assert "hcc" in keys and "hcc#0" in keys and "hcc#1" in keys, keys
    ok = [e for e in events if e.get("stage") == "hcc#0" and e.get("status") == "ok"]
    assert ok and "tier=" in ok[0].get("detail", ""), ok
    shutil.rmtree(ws, ignore_errors=True)
    return "单次核查逐篇行有独立完成状态"


def case_ui_stage_rows():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from app.main_window import MainWindow
    win = MainWindow()
    try:
        win.select_mode("sciencepal")
        win._rebuild_stages()
        win._on_stage({"stage": "match", "label": "S2 逐篇匹配", "status": "start"})
        win._on_stage({"stage": "match#0", "label": "S2 逐篇匹配 · 1/2 a.pdf",
                       "status": "ok", "ms": 10, "detail": "纳入 1 篇"})
        win._on_stage({"stage": "match#1", "label": "S2 逐篇匹配 · 2/2 b.pdf",
                       "status": "start"})
        keys = [win.stage_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(win.stage_list.count())]
        assert keys.index("match#0") == keys.index("match") + 1, keys
        assert keys.index("match#1") == keys.index("match#0") + 1, keys
        assert keys.index("integrate") > keys.index("match#1"), keys
        text = win.stage_list.item(keys.index("match#0")).text()
        assert "完成" in text, text
    finally:
        win.close()
    return "逐篇行插在父行之后、状态正确"


def main():
    check("五阶段 S2 逐篇", case_s2_per_paper)
    check("默认一次带全部", case_s2_together_by_default)
    check("单次核查逐篇合并", case_hcc_per_paper_merge)
    check("合并函数", case_merge_helpers)
    check("五阶段逐篇事件", case_planner_emits_child_keys)
    check("单次核查逐篇事件", case_hcc_emits_child_keys)
    check("流程列表逐篇行", case_ui_stage_rows)
    ok = sum(1 for r in RESULTS if r)
    print("\n%d/%d 通过" % (ok, len(RESULTS)))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
