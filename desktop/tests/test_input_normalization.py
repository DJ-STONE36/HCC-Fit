# -*- coding: utf-8 -*-
"""输入归一 P0 测试：交接表 04 的六个例子 + 术语/否定/疑似/既往/冲突 + AI 候选边界。"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.input_normalization import (  # noqa: E402
    RuleExtractor, extract_with_ai, apply_ai_candidates,
)
from app.input_normalization import load_terms  # noqa: E402

TERMS = BASE / "resources" / "terms.csv"
RULES = RuleExtractor(TERMS)

RESULTS = []


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append(True)
        print("PASS  %-42s %s" % (name, detail or ""))
    except Exception as exc:  # noqa: BLE001
        RESULTS.append(False)
        print("FAIL  %-42s %s: %s" % (name, type(exc).__name__, exc))


def case_hcc_preop():
    s = RULES.extract("HCC，准备肝切除，预测从手术日起一年复发")
    assert s.fields["disease"].normalized == "HCC"
    assert s.fields["disease"].status == "normalized"
    assert s.fields["treatment"].status == "ambiguous"       # 术式/根治性未明确
    assert s.fields["stage"].normalized == "术前"
    assert s.fields["start_point"].normalized == "手术日"
    assert s.fields["horizon"].normalized == "12个月"
    assert s.fields["outcome"].normalized == "复发"
    sc = RuleExtractor.to_agent_scenario(s)
    assert sc["use_timepoint"] == "pre_surgery", sc
    assert "12个月" in sc["outcome_type"], sc
    assert len(sc["patient_summary_zh"]) >= 8 and len(sc["task_zh"]) >= 8
    return "HCC/术前/手术日/12个月/复发"


def case_suspected_no_treatment():
    s = RULES.extract("疑似HCC，未治疗，想看预后")
    d = s.fields["disease"]
    assert d.status == "suspected" and "疑似" in d.normalized, (d.status, d.normalized)
    assert s.fields["treatment"].normalized == "未治疗"
    assert s.fields["outcome"].status == "unknown"
    ids = {f.id for f in s.followups}
    assert "q_disease" in ids and "q_outcome" in ids, ids
    return "疑似保留；未治疗；追问疾病/结局"


def case_icca():
    s = RULES.extract("肝内胆管癌术后，想看三年OS")
    d = s.fields["disease"]
    assert d.status == "out_of_scope", (d.status, d.normalized)
    assert "HCC" not in d.normalized, d.normalized
    assert s.fields["horizon"].normalized == "36个月"
    assert s.fields["outcome"].normalized == "总生存OS"
    return "iCCA 不映射 HCC，标范围外"


def case_mvi_unknown():
    s = RULES.extract("没有MVI结果")
    mvi = [x for x in s.data_available if "MVI" in x.normalized]
    assert mvi, s.data_available
    assert mvi[0].status == "unknown", mvi[0].status
    assert "阴性" not in mvi[0].normalized
    return "MVI 记录为未取得/未知，不是阴性"


def case_postop_two_years():
    s = RULES.extract("术后两年，问未来三年生存")
    assert s.fields["postop_duration"].normalized == "24个月"
    assert s.fields["horizon"].normalized == "36个月"
    assert s.fields["start_point"].normalized == "今天"
    return "已术后 24 个月 + 未来 36 个月，起点今天"


def case_conflict():
    s = RULES.extract("前句称未手术，后句称已术后")
    assert s.fields["treatment"].status == "conflict", s.fields["treatment"]
    assert any(c.field == "treatment" for c in s.conflicts)
    return "未手术 vs 已术后 → 冲突不覆盖"


def case_terminology():
    assert RULES.extract("肝癌患者").fields["disease"].status == "ambiguous"
    assert RULES.extract("肝脏肿瘤").fields["disease"].status == "unknown"
    assert RULES.extract("恶性肿瘤").fields["disease"].status == "ambiguous"
    assert RULES.extract("肝细胞癌").fields["disease"].normalized == "HCC"
    assert RULES.extract("非HCC").fields["disease"].status == "negated"
    old = RULES.extract("既往HCC，本次病灶未定").fields["disease"]
    assert old.status == "historical" and "既往" in old.normalized
    assert "HCC" not in RULES.extract("肝癌患者").fields["disease"].normalized
    return "肝癌/肝脏肿瘤/恶性肿瘤 ≠ HCC；否定/既往正确"


def case_ai_boundary():
    class FakeAI:
        def extract(self, raw, session):
            return [
                {"field": "disease", "concept_id": "disease.hcc"},
                {"field": "outcome", "concept_id": "outcome.os"},
            ]

    s = extract_with_ai("肝癌患者，想看看情况", TERMS, FakeAI())
    d = s.fields["disease"]
    assert d.status == "ambiguous", d.status          # AI 不能把肝癌提成 HCC
    ai = [c for c in d.candidates if c.get("source") == "ai"]
    assert ai and ai[0]["accepted"] is False
    assert s.fields["outcome"].normalized == "总生存OS"
    assert s.fields["outcome"].status == "ambiguous"  # AI 候选仍需确认
    return "AI 不能把肝癌变 HCC；只能补候选"


def case_save_load():
    import shutil, uuid
    s = RULES.extract("HCC，准备肝切除，预测从手术日起一年复发")
    tmp = BASE / "tests" / "_tmp_input"
    tmp.mkdir(parents=True, exist_ok=True)
    path = tmp / ("session_%s.json" % uuid.uuid4().hex[:6])
    try:
        s.save(path)
        s2 = type(s).load(path)
        assert s2.raw_text == s.raw_text
        assert s2.fields["disease"].raw == "HCC"
        assert s2.fields["disease"].source_span == s.fields["disease"].source_span
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return "原话/来源/状态可落盘重载"


def case_confirm_gate():
    s = RULES.extract("肝癌患者")
    assert not s.can_confirm(), "有必答追问时不应允许确认"
    # 医生在场景卡里把疾病改为 HCC 后，才允许确认
    s.fields["disease"].normalized = "HCC"
    s.fields["disease"].status = "normalized"
    for f in s.followups:
        f.answer_status = "answered"
    assert s.can_confirm()
    s.confirm()
    assert s.confirmation["status"] == "user_confirmed"
    assert s.version == 2
    return "必答追问未答完不能确认；确认后版本 +1"


def case_openai_provider():
    import json as _json
    from app.ai_extract import OpenAICompatibleProvider

    class Fake(OpenAICompatibleProvider):
        def __init__(self):
            super().__init__("https://example.test/v1", "k", "fake", TERMS)

        def _post(self, payload):
            body = {"fields": [
                {"field": "disease", "concept_id": "disease.hcc", "raw": "肝癌"},
                {"field": "outcome", "concept_id": "outcome.os", "raw": "生存"},
            ]}
            return {"choices": [{"message": {"content": _json.dumps(body)}}]}

    s = extract_with_ai("肝癌患者，想看看情况", TERMS, Fake())
    assert s.fields["disease"].status == "ambiguous"
    assert s.fields["outcome"].normalized == "总生存OS"
    return "OpenAI-compatible provider 可接，HCC 越权被规则拦住"


def case_gate_blockers():
    s = RULES.extract("肝内胆管癌术后，想看三年OS")
    for f in s.followups:
        f.answer_status = "answered"
    assert not s.can_confirm(), "超范围诊断不应允许进入核查"
    c = RULES.extract("前句称未手术，后句称已术后")
    for f in c.followups:
        f.answer_status = "answered"
    assert not c.can_confirm(), "冲突未解决不应允许进入核查"
    return "超范围/冲突不能确认进入核查"


def main():
    check("HCC 术前 一年复发", case_hcc_preop)
    check("疑似 HCC 未治疗 看预后", case_suspected_no_treatment)
    check("iCCA 不映射 HCC", case_icca)
    check("没有 MVI 结果是未知", case_mvi_unknown)
    check("术后两年 + 未来三年", case_postop_two_years)
    check("未手术 vs 已术后冲突", case_conflict)
    check("术语/否定/疑似/既往", case_terminology)
    check("AI 候选边界", case_ai_boundary)
    check("AI provider 接口", case_openai_provider)
    check("输入会话落盘/重载", case_save_load)
    check("确认门禁/版本", case_confirm_gate)
    check("超范围/冲突禁止确认", case_gate_blockers)
    ok = sum(1 for r in RESULTS if r)
    print("\n%d/%d 通过" % (ok, len(RESULTS)))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
