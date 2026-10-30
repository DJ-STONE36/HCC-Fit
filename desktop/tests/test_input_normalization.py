# -*- coding: utf-8 -*-
"""输入归一 P0 测试：交接表 04 的例子 + 术语/否定/疑似/既往/冲突
+ 追问抑制（sufficient）+ 空格软切分 + AI 候选层已移除。"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "core"))

from app.input_normalization import (  # noqa: E402
    RuleExtractor, split_sentences, field_sufficient, sufficient_fields,
)
from app.input_normalization import load_terms  # noqa: E402
from validate_output import validate  # noqa: E402
import json  # noqa: E402

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
    assert not s.followups, [f.id for f in s.followups]      # 原话已足够，不再追问
    assert s.can_confirm()
    sc = RuleExtractor.to_agent_scenario(s)
    assert sc["use_timepoint"] == "pre_surgery", sc
    assert "12个月" in sc["outcome_type"], sc
    assert len(sc["patient_summary_zh"]) >= 8 and len(sc["task_zh"]) >= 8
    return "HCC/术前/手术日/12个月/复发；零追问"


def case_suspected_no_treatment():
    s = RULES.extract("疑似HCC，未治疗，想看预后")
    d = s.fields["disease"]
    assert d.status == "suspected" and "疑似" in d.normalized, (d.status, d.normalized)
    assert s.fields["treatment"].normalized == "未治疗"
    assert s.fields["outcome"].status == "unknown"
    ids = {f.id for f in s.followups}
    assert "q_disease" in ids and "q_outcome" in ids, ids
    assert "q_treatment" not in ids, ids      # 已明确“未治疗”
    return "疑似保留；未治疗；只追问疾病/结局"


def case_icca():
    s = RULES.extract("肝内胆管癌术后，想看三年OS")
    d = s.fields["disease"]
    assert d.status == "out_of_scope", (d.status, d.normalized)
    assert "HCC" not in d.normalized, d.normalized
    assert s.fields["horizon"].normalized == "36个月"
    assert s.fields["outcome"].normalized == "总生存OS"
    ids = {f.id for f in s.followups}
    assert "q_disease" in ids, ids
    assert "q_outcome" not in ids, ids        # 明确写了 OS，不再问结局
    return "iCCA 不映射 HCC，标范围外；OS 明确不追问"


def case_mvi_unknown():
    s = RULES.extract("没有MVI结果")
    mvi = [x for x in s.data_available if "MVI" in x.normalized]
    assert mvi, s.data_available
    assert mvi[0].status == "unknown", mvi[0].status
    assert "阴性" not in mvi[0].normalized
    ids = {f.id for f in s.followups}
    assert "q_disease" in ids, ids            # 没提疾病 → 疾病必须问
    return "MVI 记录为未取得/未知，不是阴性；疾病未提仍追问"


def case_postop_two_years():
    s = RULES.extract("术后两年，问未来三年生存")
    assert s.fields["postop_duration"].normalized == "24个月"
    assert s.fields["horizon"].normalized == "36个月"
    assert s.fields["start_point"].normalized == "今天"
    assert field_sufficient(s, "start_point")
    assert not field_sufficient(s, "outcome")   # 只说“生存”，没说是 OS
    return "已术后 24 个月 + 未来 36 个月，起点今天；生存仍需确认"


def case_conflict():
    s = RULES.extract("前句称未手术，后句称已术后")
    assert s.fields["treatment"].status == "conflict", s.fields["treatment"]
    assert any(c.field == "treatment" for c in s.conflicts)
    assert not field_sufficient(s, "treatment")
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


def case_followup_suppression():
    """原话已有信息 → 不追问；只有缺信息才问。"""
    s = RULES.extract("58 岁男性，乙肝，肝癌，刚切完，病理还没出来，想看看三年生存。")
    ids = {f.id for f in s.followups}
    assert "q_disease" in ids, ids                  # “肝癌”≠ HCC，仍要问
    assert "q_treatment" not in ids, ids            # 刚切完 = 治疗大类已知
    assert "q_start" in ids, ids                    # 没说从哪天算
    assert "q_outcome" in ids, ids                  # 只说“生存”，未说明 OS/复发
    assert "q_intent" not in ids, ids               # 原话写了想问什么
    suf = sufficient_fields(s)
    assert suf["treatment"] is True
    assert suf["disease"] is False and suf["outcome"] is False

    full = RULES.extract("HCC，准备肝切除，从手术日起预测三年复发")
    assert not full.followups, [f.id for f in full.followups]
    assert all(full.to_dict()["sufficient"][k] for k in
               ("disease", "treatment", "outcome", "start_point", "intent"))
    return "缺什么问什么；已足够零追问"


def case_space_split():
    text = "58 岁男性 刚切完 想看看三年生存"
    segs = split_sentences(text)
    assert [s for s, _, _ in segs] == ["58 岁男性", "刚切完", "想看看三年生存"], segs
    for seg, s, e in segs:
        assert text[s:e] == seg, (seg, s, e, text[s:e])

    assert [s for s, _, _ in split_sentences("非 HCC 患者")] == ["非 HCC", "患者"]
    assert [s for s, _, _ in split_sentences("Child-Pugh A 级")] == ["Child-Pugh A", "级"]
    assert [s for s, _, _ in split_sentences("hepatocellular carcinoma 一年复发")] == [
        "hepatocellular carcinoma", "一年复发"]
    assert [s for s, _, _ in split_sentences("3 年")] == ["3 年"]
    assert [s for s, _, _ in split_sentences("1.5 cm")] == ["1.5 cm"]
    # 多个空格/全角空格/制表符是明确分隔，不保护
    assert [s for s, _, _ in split_sentences("肝癌  刚切完")] == ["肝癌", "刚切完"]
    assert [s for s, _, _ in split_sentences("肝癌\u3000刚切完")] == ["肝癌", "刚切完"]
    # 硬标点仍然切
    assert [s for s, _, _ in split_sentences("刚切完。病理未出")] == ["刚切完。", "病理未出"]
    return "空格软切分 + 数字单位/否定/拉丁名保护"


def case_space_extraction():
    s = RULES.extract("肝癌患者 刚切完 病理还没出来 想看看三年生存")
    assert s.fields["disease"].status == "ambiguous", s.fields["disease"]
    assert [x for x in s.data_available if "病理" in x.normalized], s.data_available
    assert "想看看三年生存" in s.fields["intent"].normalized, s.fields["intent"]
    assert s.fields["horizon"].normalized == "36个月"
    return "无标点、只用空格分隔也能解析"


def case_spaced_horizon():
    s = RULES.extract("HCC 准备肝切除 从手术日 想看看 3 年 OS")
    assert s.fields["horizon"].normalized == "36个月", s.fields["horizon"].normalized
    assert s.fields["outcome"].normalized == "总生存OS"
    assert s.fields["start_point"].normalized == "手术日"
    assert field_sufficient(s, "outcome")
    assert not s.followups, [f.id for f in s.followups]
    return "“3 年”带空格也能识别为 36 个月"


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
        assert s2.fields["disease"].concept_id == "disease.hcc"
        assert s2.fields["disease"].source_span == s.fields["disease"].source_span
        assert s2.to_dict()["sufficient"]["disease"] is True
        schema = json.loads(
            (BASE / "resources" / "schemas" / "input_session.schema.json").read_text(encoding="utf-8"))
        errs = validate(s2.to_dict(), schema)
        assert not errs, errs[:3]
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return "原话/来源/状态/concept_id 可落盘重载；符合 schema"


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


def case_ai_layer_removed():
    from app import input_normalization as mod
    for name in ("AIProvider", "apply_ai_candidates", "extract_with_ai", "PROTECTED"):
        assert not hasattr(mod, name), name
    try:
        import app.ai_extract  # noqa: F401
    except ImportError:
        pass
    else:
        raise AssertionError("app.ai_extract 应已删除")
    return "AI 候选层已移除（模块与函数都不存在）"


def main():
    check("HCC 术前 一年复发", case_hcc_preop)
    check("疑似 HCC 未治疗 看预后", case_suspected_no_treatment)
    check("iCCA 不映射 HCC", case_icca)
    check("没有 MVI 结果是未知", case_mvi_unknown)
    check("术后两年 + 未来三年", case_postop_two_years)
    check("未手术 vs 已术后冲突", case_conflict)
    check("术语/否定/疑似/既往", case_terminology)
    check("追问抑制（原话已足够）", case_followup_suppression)
    check("空格软切分/保护短语", case_space_split)
    check("空格分隔文本解析", case_space_extraction)
    check("带空格的范围与 OS", case_spaced_horizon)
    check("输入会话落盘/重载", case_save_load)
    check("确认门禁/版本", case_confirm_gate)
    check("超范围/冲突禁止确认", case_gate_blockers)
    check("AI 候选层已移除", case_ai_layer_removed)
    ok = sum(1 for r in RESULTS if r)
    print("\n%d/%d 通过" % (ok, len(RESULTS)))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
