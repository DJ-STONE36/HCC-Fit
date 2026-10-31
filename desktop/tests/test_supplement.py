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


def case_attachments():
    v = make_visit()
    sc = build_supplemented_scenario(v, "", attachments=["病理报告.pdf"])
    assert "报告后补充资料" in sc["patient_summary_zh"]
    assert "另附资料文件（请直接读取附件，不要当论文）：病理报告.pdf" in sc["patient_summary_zh"]
    assert "报告后补充：附件：病理报告.pdf" in sc["inputs_given"]
    # 文字 + 附件同时存在
    sc2 = build_supplemented_scenario(v, "病理：MVI 阴性", attachments=["a.pdf", "b.pdf"])
    assert "病理：MVI 阴性" in sc2["patient_summary_zh"]
    assert "a.pdf、b.pdf" in sc2["patient_summary_zh"]
    assert build_supplemented_scenario(v, "", attachments=[]) == v["scenario"]
    return "附件事项并入场景（只有附件也能重跑）"


def case_attachment_prompt():
    from hcc_report import attachments_block, build_prompt
    assert attachments_block([]) == ""
    blk = attachments_block(["病理报告.pdf"])
    assert "病理报告.pdf" in blk and "不是论文" in blk and "unknown" in blk
    prompt = build_prompt({"patient_summary_zh": "x"}, "", ["论文.pdf"],
                          attachments=["病理报告.pdf"])
    assert "报告后补充的患者资料（附件，不是论文）" in prompt
    assert prompt.index("病理报告.pdf") > prompt.index("论文.pdf")
    return "单次核查提示词含“患者资料、不是论文”说明"


def case_planner_payload():
    from planner import Planner

    seen = []

    class RecBrain:
        name = "rec"
        opts = {}

        def run_stage(self, key, payload):
            seen.append(payload)
            return {
                "domain": {"status": "in_scope", "note_zh": "ok"},
                "papers": {"papers": []},
                "match": {"matched": []},
                "integrate": {"summary_zh": "", "options": [], "cautions_zh": []},
                "expected": {"expected_data": []},
            }[key]

    tmp = BASE / "tests" / "_tmp_plan_att"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        p = Planner(RecBrain(), str(tmp), {"task_zh": "t"}, files=["论文.pdf"],
                    attachments=["病理报告.pdf"])
        visit = p.run()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    assert seen and all(x.get("attachments") == ["病理报告.pdf"] for x in seen), seen
    assert all(x.get("files") == ["论文.pdf"] for x in seen)
    assert visit["provenance"]["attachments"] == ["病理报告.pdf"]
    return "五阶段每阶段都带 attachments；论文与附件分开"


def case_hcc_attachments():
    """单次核查：附件进 prompt、进上传列表、进 provenance（不混进 papers）。"""
    import hcc_report
    from planner import HccPlanner

    calls = {}

    class B:
        name = "fake"
        agent_name = "HCC-Fit"

        def run_hcc_report(self, prompt, files):
            calls["prompt"] = prompt
            calls["files"] = list(files)
            return {"_parse": "text", "_text": "x"}

    tmp = BASE / "tests" / "_tmp_hcc_att"
    shutil.rmtree(tmp, ignore_errors=True)
    (tmp / "files").mkdir(parents=True, exist_ok=True)
    orig = hcc_report.normalize_report
    hcc_report.normalize_report = lambda *a, **k: {"matched": [], "provenance": {"files": []}}
    try:
        p = HccPlanner(B(), str(tmp), {"patient_summary_zh": "x", "task_zh": "y"},
                       files=["论文.pdf"], attachments=["病理报告.pdf"])
        visit = p._run_one(["论文.pdf"])
    finally:
        hcc_report.normalize_report = orig
        shutil.rmtree(tmp, ignore_errors=True)
    assert "报告后补充的患者资料（附件，不是论文）" in calls["prompt"]
    assert "病理报告.pdf" in calls["prompt"]
    assert [Path(f).name for f in calls["files"]] == ["论文.pdf", "病理报告.pdf"], calls["files"]
    assert visit["provenance"]["attachments"] == ["病理报告.pdf"]
    return "单次核查：附件进 prompt / 上传列表 / provenance"


def main():
    tests = [("拼接 scenario", case_build), ("补充记录", case_model),
             ("数组输入", case_split_list), ("空输入", case_empty),
             ("PDF 附件", case_attachments), ("附件提示词", case_attachment_prompt),
             ("附件透传", case_planner_payload), ("单次核查附件", case_hcc_attachments)]
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
