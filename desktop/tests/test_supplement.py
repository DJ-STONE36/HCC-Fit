# -*- coding: utf-8 -*-
"""报告后补充资料：拼 scenario / 保留原缺失项 / 落盘。"""
import copy
import shutil
import sys
import uuid
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "core"))

from app.supplement import Supplement, build_supplemented_scenario  # noqa: E402


def make_visit():
    return {
        "visit_id": "visit_1793250000",
        "mode": "sciencepal_hcc",
        "provenance": {"input_id": "in_20261029_abcdef12"},
        "scenario": {
            "task_zh": "评估该患者是否适用 1 年内复发模型",
            "patient_summary_zh": "疾病：HCC；治疗：已接受切除；阶段：治疗后，病理未取得",
            "use_timepoint": "post_surgery",
            "outcome_type": "复发（12个月）",
            "inputs_given": ["病理未取得"],
            "inputs_missing": ["病理未取得", "MVI未取得"],
            "paper_text": "论文正文……",
        },
        "expected_data": [
            {"item_zh": "病理分级 / MVI", "why_zh": "决定模型输入是否完整",
             "unlocks_zh": "可能从不可讨论转为有条件可用", "kind": "pathology"},
            {"item_zh": "手术日期", "why_zh": "计算起点", "unlocks_zh": "明确 12 个月窗口",
             "kind": "history"},
        ],
    }


def case_build():
    v = make_visit()
    before = copy.deepcopy(v)
    sc = build_supplemented_scenario(v, "病理：中分化 HCC，MVI 阴性；手术日期 2026-10-20")
    assert "报告后补充资料" in sc["patient_summary_zh"]
    assert "报告后补充：" in sc["inputs_given"]
    assert "MVI未取得" in sc["inputs_missing"]          # 不假装已补齐
    assert isinstance(sc["inputs_given"], str)
    assert isinstance(sc["inputs_missing"], str)
    assert sc["task_zh"] == before["scenario"]["task_zh"]
    assert v == before                                   # 原 visit 不变
    return "只追加，不覆盖；缺失项保留"


def case_model():
    v = make_visit()
    s = Supplement.from_visit(v, "病理：中分化 HCC，MVI 阴性")
    assert s.parent_visit_id == "visit_1793250000"
    assert s.parent_input_id == "in_20261029_abcdef12"
    assert len(s.expected_items) == 2
    assert all(not x.checked for x in s.expected_items)
    s.expected_items[0].checked = True
    tmp = BASE / "tests" / "_tmp_supp"
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / ("%s.json" % uuid.uuid4().hex[:6])
    try:
        s.save(path)
        got = Supplement.load(path)
        assert got.supplement_id == s.supplement_id
        assert got.raw_text == s.raw_text
        assert got.expected_items[0].checked is True
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return "parent/预期项/落盘正常"


def case_split_list():
    from core.planner import _split
    assert _split(["病理", "MVI"]) == ["病理", "MVI"]
    return "Planner._split 兼容数组"


def case_empty():
    v = make_visit()
    sc = build_supplemented_scenario(v, "   ")
    assert sc["patient_summary_zh"] == v["scenario"]["patient_summary_zh"]
    assert sc["inputs_given"] == v["scenario"]["inputs_given"]
    return "空补充不改 scenario"


def main():
    tests = [("拼接 scenario", case_build), ("补充记录", case_model),
             ("数组输入", case_split_list), ("空输入", case_empty)]
    ok = 0
    for name, fn in tests:
        try:
            print("PASS  %-14s %s" % (name, fn()))
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print("FAIL  %-14s %s: %s" % (name, type(exc).__name__, exc))
    print("\n%d/%d 通过" % (ok, len(tests)))
    return 0 if ok == len(tests) else 1


if __name__ == "__main__":
    sys.exit(main())
