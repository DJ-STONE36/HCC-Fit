# -*- coding: utf-8 -*-
"""
contract.py — "宽进严出"的返回规范层。

设计原则（来自实际踩坑）：
  · **不阻碍 Agent 输出**：它爱用中文键、爱加字段、爱嵌套，都行；
  · 但前端只认少数几个字段，所以这里做"最小必需 + 别名映射 + 缺省填充"；
  · 任何解析失败都退化为"原文"，绝不因为格式问题让整条链路失败。

每个 normalize_* 只保证「前端能渲染」，不追求把信息抽全——
丢了的字段由上层用 unknown/空数组表示，这是刻意的。
"""

from __future__ import annotations

import re

STATUS_MAP = {
    "match": ["match", "matched", "匹配", "符合", "一致", "对得上", "yes", "true", "是"],
    "mismatch": ["mismatch", "不匹配", "不符", "不一致", "不是", "冲突", "对不上"],
    "unknown": ["unknown", "uncertain", "unclear", "无法确认", "不确定", "待定",
                "不清楚", "未知", "maybe", "未报告", "未给出", "没有资料", "资料不足",
                "not reported", "no data", "missing"],
}

# 只有“整串就是这个值”才算，避免 "unknown" 里的 "no" 被当成 mismatch
EXACT_STATUS = {
    "no": "mismatch", "false": "mismatch", "否": "mismatch", "不是": "mismatch",
    "yes": "match", "true": "match", "是": "match",
}

TIER_MAP = {
    "applicable": ["applicable", "可用", "可直接讨论", "可以直接讨论", "匹配可用", "建议可用"],
    "conditional": ["conditional", "有条件", "有条件可用", "谨慎可用"],
    "undetermined": ["undetermined", "需补数据", "待补", "无法确认", "unknown", "证据不足"],
    "not_applicable": ["not_applicable", "不适用", "不可用", "不建议", "排除", "excluded"],
}

DIM_ALIAS = {
    "population": ["population", "人群", "人群匹配", "患者人群"],
    "timepoint": ["timepoint", "时点", "时点与输入", "使用时点", "时间点"],
    "input": ["input", "输入", "变量", "变量可得性", "所需变量"],
    "outcome": ["outcome", "结局", "终点", "预测结局"],
}

DIM_KEYS = ("population", "timepoint", "input", "outcome")


def _first(obj, keys):
    """在 dict 里按（中英）别名找第一个非空值；支持嵌套一层。"""
    if not isinstance(obj, dict):
        return None
    for k in keys:
        if k in obj and obj[k] not in (None, "", [], {}):
            return obj[k]
    for v in obj.values():
        if isinstance(v, dict):
            for k in keys:
                if k in v and v[k] not in (None, "", [], {}):
                    return v[k]
    return None


def _as_list(v):
    if isinstance(v, list):
        return [x for x in v if x is not None]
    if isinstance(v, dict):
        # {"p1": {...}} 这种按 id 索引的写法
        out = []
        for k, x in v.items():
            if isinstance(x, dict):
                x = dict(x)
                x.setdefault("paper_id", k)
                out.append(x)
        return out if out else [v]
    return []


def _s(v, limit=400):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        import json
        v = json.dumps(v, ensure_ascii=False)
    return re.sub(r"\s+", " ", str(v)).strip()[:limit]


def _status(v, default="unknown"):
    """三值归一。先整串精确匹配，再按词表判断。

    注意：不能把 "no"/"false" 放进子串表——`unknown` 里就有 "no"，
    会把“无法确认”误判成“不匹配”（这是产品语义，不能错）。
    """
    s = _s(v, 60).strip().lower()
    if s in EXACT_STATUS:
        return EXACT_STATUS[s]
    for canon in ("mismatch", "unknown", "match"):
        if any(w.lower() in s for w in STATUS_MAP[canon]):
            return canon
    return default


def _tier(v, default="undetermined"):
    """同样先判否定（不可用），再判需补数据，最后才是可用。"""
    s = _s(v, 60).lower()
    for canon in ("not_applicable", "undetermined", "conditional", "applicable"):
        if any(w.lower() in s for w in TIER_MAP[canon]):
            return canon
    return default


def _dim_id(v):
    s = _s(v, 40).lower()
    for canon, words in DIM_ALIAS.items():
        if any(w.lower() in s for w in words):
            return canon
    return s or "unknown"


# ---------------- 各阶段的"最小必需"归一 ----------------
def normalize_domain(obj):
    raw = _s(_first(obj, ["status", "state", "结论", "判定", "result"]), 80).lower()
    if any(w in raw for w in ("outside_scope", "out_of_scope", "not_in_scope",
                              "不在范围", "不属于", "范围外", "超出范围")):
        status = "outside_scope"
    elif any(w in raw for w in ("in_scope", "in-scope", "在范围内", "属于范围", "范围内")):
        status = "in_scope"
    elif any(w in raw for w in ("unclear", "无法确认", "不确定", "待定")):
        status = "unclear"
    else:
        status = _status(raw, "unclear")
        if status == "match":
            status = "in_scope"
        elif status == "mismatch":
            status = "outside_scope"
        elif status == "unknown":
            status = "unclear"
    note = _s(_first(obj, ["note_zh", "note", "reason", "理由", "说明", "noteZh"]), 280)
    return {"status": status, "note_zh": note or "Agent 未给出理由"}


def normalize_papers(obj):
    raw = _first(obj, ["papers", "candidates", "候选", "论文", "items", "list"])
    papers, seen = [], set()
    for i, p in enumerate(_as_list(raw)):
        if isinstance(p, str):
            p = {"title": p}
        if not isinstance(p, dict):
            continue
        pid = _s(_first(p, ["paper_id", "id", "编号"]), 32) or ("p%d" % (i + 1))
        if pid in seen:
            pid = pid + "_%d" % i
        seen.add(pid)
        title = _s(_first(p, ["title", "标题", "论文标题", "name"]), 200) or "未命名论文"
        decision_raw = _s(_first(p, ["decision", "结论", "是否纳入", "include", "verdict"]), 40).lower()
        exclude = (any(w in decision_raw for w in ("exclude", "排除", "不纳入", "拒绝", "reject", "not include"))
                   or decision_raw in ("no", "false", "否", "不是"))
        papers.append({
            "paper_id": pid,
            "title": title,
            "doi": _s(_first(p, ["doi", "DOI"]), 120) or None,
            "year": _int(_first(p, ["year", "年份"])),
            "source": "search" if "search" in _s(_first(p, ["source", "来源"])).lower() else "uploaded",
            "decision": "exclude" if exclude else "include",
            "reason_zh": _s(_first(p, ["reason_zh", "reason", "理由", "原因"]), 120) or "（未给理由）",
            "file_path": _s(_first(p, ["file_path", "path", "路径"]), 300) or None,
        })
    return {"papers": papers}


def normalize_match(obj):
    raw = _first(obj, ["matched", "matches", "匹配", "results", "cards", "items"])
    out = []
    for i, m in enumerate(_as_list(raw)):
        if not isinstance(m, dict):
            continue
        pid = _s(_first(m, ["paper_id", "id", "编号"]), 32) or ("p%d" % (i + 1))
        dims = _first(m, ["dimensions", "checks", "维度", "dims"])
        norm_dims = []
        if isinstance(dims, dict):
            for k, v in dims.items():
                norm_dims.append({"id": _dim_id(k), "status": _status(v),
                                  "reason_zh": _s(v, 120) if isinstance(v, str) else "",
                                  "quote": None, "location": None})
        else:
            for d in _as_list(dims):
                if isinstance(d, str):
                    norm_dims.append({"id": _dim_id(d), "status": "unknown", "reason_zh": "", "quote": None, "location": None})
                    continue
                if not isinstance(d, dict):
                    continue
                norm_dims.append({
                    "id": _dim_id(_first(d, ["id", "dimension", "维度", "name"])),
                    "status": _status(_first(d, ["status", "状态", "verdict"])),
                    "reason_zh": _s(_first(d, ["reason_zh", "reason", "理由"]), 160),
                    "quote": _s(_first(d, ["quote", "原文", "证据"]), 200) or None,
                    "location": _s(_first(d, ["location", "位置", "loc"]), 40) or None,
                })
        # 缺的维度补 unknown（宁可少，不要编）
        have = {d["id"] for d in norm_dims}
        for dim in DIM_KEYS:
            if dim not in have:
                norm_dims.append({"id": dim, "status": "unknown", "reason_zh": "未给出该项判断", "quote": None, "location": None})
        out.append({
            "paper_id": pid,
            "dimensions": norm_dims,
            "tier": _tier(_first(m, ["tier", "档位", "结论", "verdict"])),
            "evidence_strength": _s(_first(m, ["evidence_strength", "证据强度"]), 20).lower() or None,
        })
    return {"matched": out}


def normalize_integrate(obj):
    summary = _s(_first(obj, ["summary_zh", "summary", "总结", "结论", "综合结论"]), 600)
    opts_raw = _first(obj, ["options", "方案", "recommendations", "suggestions"])
    options = []
    for o in _as_list(opts_raw):
        if isinstance(o, str):
            o = {"name": o}
        if not isinstance(o, dict):
            continue
        options.append({
            "name": _s(_first(o, ["name", "方案名", "title", "方案"]), 120) or "未命名方案",
            "basis_paper_ids": [str(x) for x in _as_list(_first(o, ["basis_paper_ids", "依据", "based_on"]))],
            "confidence": _conf(_first(o, ["confidence", "置信度"])),
            "note_zh": _s(_first(o, ["note_zh", "note", "说明", "理由"]), 300),
            "what_it_needs": [str(x) for x in _as_list(_first(o, ["what_it_needs", "需要", "前提"]))],
        })
    cautions = [_s(x, 200) for x in _as_list(_first(obj, ["cautions_zh", "cautions", "注意", "注意事项", "风险"]))]
    return {"summary_zh": summary or "（Agent 未给综合结论）", "options": options,
            "cautions_zh": cautions or ["结论需人工复核"]}


def normalize_expected(obj):
    raw = _first(obj, ["expected_data", "expected", "期望数据", "需要补充", "gaps", "items"])
    out = []
    for e in _as_list(raw):
        if isinstance(e, str):
            e = {"item_zh": e}
        if not isinstance(e, dict):
            continue
        out.append({
            "item_zh": _s(_first(e, ["item_zh", "item", "项目", "要补的东西", "name"]), 120) or "未命名项",
            "why_zh": _s(_first(e, ["why_zh", "why", "为什么", "理由"]), 200) or "Agent 未说明原因",
            "unlocks_zh": _s(_first(e, ["unlocks_zh", "unlocks", "解锁"]), 200) or None,
            "cost_hint_zh": _s(_first(e, ["cost_hint_zh", "cost", "成本"]), 80) or None,
            "kind": _s(_first(e, ["kind", "类型"]), 20).lower() or None,
        })
    return {"expected_data": out}


def _conf(v, default="low"):
    """置信度归一。先判否定/低（'不高' 不能因为含 '高' 就成 high）。"""
    s = _s(v, 20).lower()
    if any(w in s for w in ("不高", "低", "弱", "low", "weak", "insufficient")):
        return "low"
    if any(w in s for w in ("moderate", "medium", "中等", "中")):
        return "moderate"
    if any(w in s for w in ("high", "高", "强")):
        return "high"
    return default


def _int(v):
    try:
        return int(re.sub(r"[^0-9]", "", str(v)) or 0) or None
    except Exception:
        return None


NORMALIZERS = {
    "domain": normalize_domain,
    "papers": normalize_papers,
    "match": normalize_match,
    "integrate": normalize_integrate,
    "expected": normalize_expected,
}


def normalize_stage(stage, obj):
    fn = NORMALIZERS.get(stage)
    if not fn:
        return obj
    try:
        return fn(obj)
    except Exception:
        return obj
