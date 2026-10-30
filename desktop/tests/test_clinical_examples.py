# -*- coding: utf-8 -*-
"""临床口述例子回归：场景卡字段提取（2026-10-30 用户给的 5 例）。"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.input_normalization import RuleExtractor, field_sufficient  # noqa: E402

TERMS = BASE / "resources" / "terms.csv"
RULES = RuleExtractor(TERMS)

CASES = [
    "肝癌切除术后 2 年，现在看看复查的情况都挺好的。现在看看他，像他这种情况大概还有多长时间会复发？",
    "刚开完刀，根治性手术，干细胞一个月复查也挺好的。然后预期他会多长时间复发？",
    "肿瘤 10 公分，根治性切除一个月。然后有癌栓，也有镜下癌栓。男性的患者，他预计多长时间会复发？大概能活多长时间？",
    "肝癌根治性切除巴塞罗那分期 B 期，那么他术后可能多长时间会复发呀？",
    "肝细胞癌切除，根治性切除后，当时分期是发现零到 A 期，现在术后一个月，预期他的总的生存期多长时间？",
]

RESULTS = []


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append(True)
        print("PASS  %-36s %s" % (name, detail or ""))
    except Exception as exc:  # noqa: BLE001
        RESULTS.append(False)
        print("FAIL  %-36s %s: %s" % (name, type(exc).__name__, exc))


def case_1():
    s = RULES.extract(CASES[0])
    assert s.fields["disease"].status == "ambiguous"          # 肝癌 ≠ HCC
    assert s.fields["postop_duration"].normalized == "24个月"
    assert s.fields["start_point"].normalized == "今天"        # “现在看看” → 从今天起算
    assert "复发" in s.fields["intent"].normalized
    assert "多长时间" in s.fields["intent"].normalized
    ids = {q.id for q in s.followups}
    assert ids == {"q_disease", "q_horizon"}, ids            # 缺疾病类型 + 预测范围
    return "术后2年/今天起算/只问疾病与范围/意图正确"


def case_2():
    s = RULES.extract(CASES[1])
    assert s.fields["treatment"].raw.startswith("刚开完刀")
    assert s.fields["stage"].normalized == "术后"
    assert s.fields["postop_duration"].normalized == "1个月"
    assert "复发" in s.fields["intent"].normalized
    ids = {q.id for q in s.followups}
    assert "q_disease" in ids and "q_start" in ids, ids
    return "刚开完刀→切除/术后；一个月复查→1个月；意图正确"


def case_3():
    s = RULES.extract(CASES[2])
    data = [d.normalized for d in s.data_available]
    assert any("10 cm" in x for x in data), data
    assert any("癌栓" in x or "血管侵犯" in x for x in data), data
    assert s.fields["postop_duration"].normalized == "1个月"
    assert s.fields["stage"].normalized == "术后"
    assert "复发" in s.fields["intent"].normalized
    assert "活" in s.fields["intent"].normalized                # 两个问题都保留
    assert s.fields["disease"].status == "unknown"              # 原话没提疾病
    return "肿瘤大小/癌栓/术后1月/两个问题都进意图"


def case_4():
    s = RULES.extract(CASES[3])
    data = [d.normalized for d in s.data_available]
    assert any("BCLC B" in x for x in data), data
    assert s.fields["disease"].status == "ambiguous"
    assert "复发" in s.fields["intent"].normalized
    return "BCLC B 期识别；疾病仍追问"


def case_5():
    s = RULES.extract(CASES[4])
    assert s.fields["disease"].normalized == "HCC"
    assert s.fields["postop_duration"].normalized == "1个月"
    assert s.fields["start_point"].normalized == "今天"
    assert field_sufficient(s, "outcome") is True               # “总的生存期”=明确 OS
    data = [d.normalized for d in s.data_available]
    assert any("0-A" in x for x in data), data
    ids = {q.id for q in s.followups}
    assert ids == {"q_horizon"}, ids          # 只差“想预测多长时间”
    return "HCC/术后1月/OS 足够 → 只问预测范围"



def case_6_official_example():
    text = ("58 岁男性，乙肝，肝癌，刚做完切除，病理还没出来，从今天开始，"
            "想看看三年总生存率；如果做模型核查，请说明是否 HCC、切除是否根治。")
    s = RULES.extract(text)
    # “请说明是否 HCC”是待确认表述，不能直接归一为 HCC
    assert s.fields["disease"].status == "ambiguous", s.fields["disease"]
    assert s.fields["start_point"].normalized == "今天"
    assert s.fields["horizon"].normalized == "36个月"
    assert field_sufficient(s, "outcome") is True          # “总生存率”=明确 OS
    assert {q.id for q in s.followups} == {"q_disease"}
    assert "总生存" in s.fields["intent"].normalized
    return "官方示例：是否 HCC 不被当成诊断；只问疾病"


def main():
    check("例1 术后2年看复发", case_1)
    check("例2 刚开完刀+1月复查", case_2)
    check("例3 大小/癌栓/两个问题", case_3)
    check("例4 BCLC B 期", case_4)
    check("例5 总生存期", case_5)
    check("官方示例（是否 HCC）", case_6_official_example)
    ok = sum(1 for r in RESULTS if r)
    print("\n%d/%d 通过" % (ok, len(RESULTS)))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
