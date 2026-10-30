# -*- coding: utf-8 -*-
"""
hcc_report.py — SciencePal HCC-Fit Agent 的"单次核查"提示词与报告归一。

Agent 的天然输出是一份四维核查报告（population/timepoint/input/outcome +
validation），而 APP 的产物是 visit.json。本模块只做两件事：

1. 把本地场景 + 论文拼成一条 prompt（一次调用，不拆五阶段）；
2. 把 Agent 返回的报告归一成 visit.json，前端现有渲染不用改。

容错：
- 认得 hcc_fit_api 那套键（population_check / timepoint_check / ...）；
- 也认得已经接近 visit 的键（matched / dimensions）；
- 纯文本/Markdown 则降级为原文，不白屏、不丢内容。
"""

from __future__ import annotations

import json
import os
import uuid
import re
import time

from contract import _status

MODE = "sciencepal_hcc"

DISCLAIMER = ("本结果仅用于临床决策前的证据准备，不构成诊断，不输出个体化概率，"
              "不替代多学科讨论与主治医师判断。")

TIMEPOINT_ZH = {
    "pre_surgery": "术前（切除/消融前）",
    "pre_treatment": "治疗前（TACE/系统治疗）",
    "post_surgery": "术后",
    "at_recurrence": "复发后",
    "surveillance": "随访",
}

SCHEMA_HINT = """{
  "population_check": {
    "verdict": "match | mismatch | unclear",
    "comparison": "论文人群与目标人群的逐项比较",
    "evidence": [{"quote": "论文原文", "location": "页码/表格/图号"}]
  },
  "timepoint_check": {
    "verdict": "match | mismatch | unclear",
    "paper_timepoint": "论文的测量/使用时间点",
    "target_timepoint": "目标时点",
    "predictors": ["模型所需变量"],
    "availability_analysis": "各变量在目标时点是否可得",
    "evidence": [{"quote": "论文原文", "location": "页码/表格/图号"}]
  },
  "input_check": {
    "verdict": "match | mismatch | unclear",
    "reason": "输入可得性的总体判断",
    "variables": [{"name": "变量名", "available": true}],
    "evidence": [{"quote": "论文原文", "location": "页码/表格/图号"}]
  },
  "outcome_check": {
    "verdict": "match | mismatch | unclear",
    "comparison": "结局定义与时间范围的比较",
    "evidence": [{"quote": "论文原文", "location": "页码/表格/图号"}]
  },
  "validation_evidence": {
    "development_performance": "开发集表现",
    "internal_validation": "内部验证",
    "external_validation": "外部验证（没有就写“未报告”）",
    "validation_population": "验证人群",
    "not_reported": ["论文未报告的内容"]
  },
  "expected_data": [
    {"item": "还缺的数据", "why": "为什么需要", "unlocks": "补齐能解锁什么", "cost": "获取成本", "kind": "lab|imaging|pathology|history|other"}
  ],
  "overall_verdict": "match | mismatch | unclear",
  "overall_summary": "一段总体结论",
  "limitations": ["本次核查的局限"]
}"""


def _s(v, limit=600):
    if v is None:
        return ""
    if isinstance(v, (dict, list)):
        v = json.dumps(v, ensure_ascii=False)
    return re.sub(r"\s+", " ", str(v)).strip()[:limit]


def _list(v):
    if isinstance(v, list):
        return [x for x in v if x is not None]
    if isinstance(v, str) and v.strip():
        return [v]
    return []


def _title_from_name(name):
    base = os.path.splitext(os.path.basename(name or ""))[0]
    title = base.replace("_", " ").replace("-", " ").strip()
    if len(title) < 6:
        title = (title + "（上传论文）").strip()
    return title[:120] or "上传论文（未命名）"


def build_prompt(scenario, paper_text, file_names):
    """把场景 + 论文拼成一次 Agent 调用。"""
    tp = TIMEPOINT_ZH.get(scenario.get("use_timepoint") or "", scenario.get("use_timepoint") or "未指定")
    files = "、".join(file_names) if file_names else "（无附件，正文见下）"
    text = (paper_text or "").strip()
    if len(text) > 30000:
        text = text[:30000] + "\n...[文本过长，已截断]"
    return (
        "你是 HCC-Fit，肝细胞癌（HCC）预测模型场景适配核查助手。\n"
        "请只针对这一篇论文、这一个场景做核查，只输出一个 JSON（不要 Markdown 代码块、不要解释）。\n"
        "如果平台支持工具：可以先读附件/检索；**读完后必须输出下面的 JSON 作为最后一条消息**，"
        "不要停在工具调用、执行结果或“我准备读…”这类过程说明上。\n\n"
        "【工作原则】\n"
        "- 严谨中立：不因模型性能高就忽略场景冲突；\n"
        "- 证据导向：重要判断附论文原文 quote 与可定位 location；找不到原文就留空并把 verdict 写 unclear；\n"
        "- 论文没报告的信息写进 not_reported，绝不编造；\n"
        "- 不计算个体风险、不推荐治疗方案、不宣称模型可直接用于临床。\n\n"
        "【临床场景】\n"
        "- 目标患者人群：%s\n"
        "- 要回答的问题：%s\n"
        "- 使用时点：%s\n"
        "- 关心的结局：%s\n"
        "- 已有资料：%s\n"
        "- 缺失资料：%s\n\n"
        "【论文】\n"
        "- 附件：%s（若平台能读附件，请直接读；读不到再用下面的文字）\n"
        "- 正文/关键段落：\n%s\n\n"
        "【输出结构｜严格按这个 JSON】\n%s\n\n"
        "注意：verdict 只能是 match / mismatch / unclear；input_check 要逐变量说明目标时点是否可得；"
        "expected_data 要写清“还缺什么、为什么、补齐能解锁什么”。\n\n"
        "【最终输出要求｜必须遵守】\n"
        "现在只输出一个 JSON 对象。不要写任何分析、Markdown、代码块标记或前后说明；"
        "第一个字符必须是 {，最后一个字符必须是 }。\n"
        "论文摘要、公式、计算过程都不要放在最终消息里——只放核查结论。"
        % (
            _s(scenario.get("patient_summary_zh"), 1200) or "未填写",
            _s(scenario.get("task_zh"), 800) or "未填写",
            tp,
            _s(scenario.get("outcome_type"), 300) or "未指定",
            _s(scenario.get("inputs_given"), 400) or "未填写",
            _s(scenario.get("inputs_missing"), 400) or "未填写",
            files,
            text or "（未提供正文；请只基于附件核查，信息不足就写 unclear）",
            SCHEMA_HINT,
        )
    )


# ---------------- 归一 ----------------

def _dim(check, fallback_reason):
    check = check if isinstance(check, dict) else {}
    status = _status(check.get("verdict"))
    ev = _list(check.get("evidence"))
    first = ev[0] if ev and isinstance(ev[0], dict) else {}
    reason = _s(check.get("comparison") or check.get("availability_analysis")
                or check.get("reason") or fallback_reason, 200)
    return {
        "status": status,
        "reason_zh": reason or fallback_reason,
        "quote": _s(first.get("quote"), 200) or None,
        "location": _s(first.get("location"), 40) or None,
    }


def _input_dim(report):
    ic = report.get("input_check")
    if isinstance(ic, dict) and ic:
        return _dim(ic, "Agent 未单独说明输入可得性")

    tp = report.get("timepoint_check") if isinstance(report.get("timepoint_check"), dict) else {}
    variables = [v for v in _list(tp.get("variables")) if isinstance(v, dict)]
    status = "unknown"
    if variables:
        unavailable = [v for v in variables if v.get("available") is False or v.get("available_at_target") is False]
        available = [v for v in variables if v.get("available") is True or v.get("available_at_target") is True]
        if unavailable:
            status = "mismatch"
        elif available and len(available) == len(variables):
            status = "match"
    else:
        text = _s(tp.get("availability_analysis"), 300)
        if re.search(r"不可得|无法获得|无法获取|未获取|不可用|术前不可|缺失", text):
            status = "mismatch"
        elif re.search(r"均可|可得|可获得|能够获得|齐全|已具备", text):
            status = "match"
    reason = _s(tp.get("availability_analysis"), 200) or "Agent 未单独给出输入可得性判断"
    return {"status": status, "reason_zh": reason, "quote": None, "location": None}


def _strength(ve):
    ve = ve if isinstance(ve, dict) else {}
    ext = _s(ve.get("external_validation") or ve.get("external"), 200)
    internal = _s(ve.get("internal_validation") or ve.get("internal"), 200)
    dev = _s(ve.get("development_performance") or ve.get("development"), 200)

    def reported(x):
        return bool(x) and ("未报告" not in x) and ("not reported" not in x.lower())

    if reported(ext):
        return "strong"
    if reported(internal):
        return "moderate"
    if reported(dev):
        return "weak"
    return "insufficient"


def _tier(dims, strength, overall):
    statuses = [d["status"] for d in dims]
    if "mismatch" in statuses:
        return "not_applicable"
    if "unknown" in statuses:
        return "undetermined"
    if _status(overall) == "mismatch":
        return "undetermined"          # 总体说冲突、分维却说匹配：不硬给 applicable
    if strength == "strong":
        return "applicable"
    return "conditional"


def _expected(report):
    out = []
    for e in _list(report.get("expected_data")):
        if isinstance(e, str):
            e = {"item": e}
        if not isinstance(e, dict):
            continue
        item = _s(e.get("item_zh") or e.get("item") or e.get("name"), 120)
        why = _s(e.get("why_zh") or e.get("why"), 200)
        if not item:
            continue
        kind = _s(e.get("kind"), 20).lower()
        if kind not in ("lab", "imaging", "pathology", "history", "other"):
            kind = "other"
        out.append({
            "item_zh": item,
            "why_zh": why or "Agent 未说明原因",
            "unlocks_zh": _s(e.get("unlocks_zh") or e.get("unlocks"), 200) or None,
            "cost_hint_zh": _s(e.get("cost_hint_zh") or e.get("cost"), 80) or None,
            "kind": kind,
        })
    return out


def _uncertainties(dims, ve, limitations):
    out = []
    for d in dims:
        if d["status"] == "unknown":
            out.append("「%s」无法确认：%s" % (d["id"], d["reason_zh"]))
    for x in _list(ve.get("not_reported") if isinstance(ve, dict) else None):
        text = _s(x, 200)
        if text:
            out.append("论文未报告：%s" % text)
    for x in limitations:
        text = _s(x, 200)
        if text:
            out.append(text)
    seen, uniq = set(), []
    for x in out:
        if x not in seen:
            seen.add(x)
            uniq.append(x)
    if not uniq:
        uniq.append("本次未记录到显式不确定项，但仍需人工复核论文结论与适用边界。")
    return uniq[:8]


def _papers(files, workspace):
    name = files[0] if files else None
    title = _title_from_name(name) if name else "上传论文（未命名）"
    path = os.path.join(workspace, "files", name) if name else None
    return [{
        "paper_id": "p1",
        "title": title,
        "doi": None,
        "year": None,
        "source": "uploaded",
        "decision": "include",
        "reason_zh": "由 SciencePal HCC-Fit Agent 单篇核查",
        "file_path": path,
    }]


def _scenario(scenario):
    def split(text):
        if isinstance(text, (list, tuple)):
            return [str(p).strip() for p in text if str(p).strip()]
        if not text:
            return []
        for sep in ("，", ",", "、", ";", "；", "\n"):
            text = text.replace(sep, "|")
        return [p.strip() for p in text.split("|") if p.strip()]

    task = scenario.get("task_zh") or "未填写"
    patient = scenario.get("patient_summary_zh") or "未填写"
    if len(task) < 8:                    # schema 要求 minLength 8
        task = (task + "（需补充具体问题）").strip()
    if len(patient) < 8:
        patient = (patient + "（需补充患者信息）").strip()
    return {
        "task_zh": task,
        "patient_summary_zh": patient,
        "use_timepoint": scenario.get("use_timepoint") or "未指定",
        "outcome_type": scenario.get("outcome_type") or "未指定",
        "inputs_given": split(scenario.get("inputs_given")),
        "inputs_missing": split(scenario.get("inputs_missing")),
    }


def normalize_report(report, scenario, files, workspace, raw_text, elapsed_ms, agent_name=None):
    """Agent 返回 → visit.json。"""
    report = report if isinstance(report, dict) else {}
    files = files or []
    papers = _papers(files, workspace)
    raw_text = raw_text or ""

    if report.get("_parse") == "text":
        text = _s(report.get("_text") or raw_text, 8000)
        summary = text[:600] or "Agent 未按 JSON 返回"
        if len(summary) < 8:
            summary = (summary + "；请阅读原文").strip()
        visit = {
            "schema_version": "0.1.0",
            "visit_id": "visit_%d_%s" % (int(time.time()), uuid.uuid4().hex[:6]),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "mode": MODE,
            "scenario": _scenario(scenario),
            "domain": {"status": "unclear", "note_zh": "Agent 返回非结构化内容，域判定需人工确认"},
            "papers": papers,
            "matched": [],
            "integrated": {
                "summary_zh": summary,
                "options": [],
                "cautions_zh": ["Agent 未按 JSON 返回，以下为原文摘要，请人工判读"],
            },
            "expected_data": [],
            "parse_mode": "text_fallback",
            "raw_excerpts": [{"stage": "hcc", "text": text[:4000]}],
            "raw_answers": [{"stage": "hcc", "text": text[:8000]}],
            "uncertainties": ["Agent 未按 JSON 返回，结论不可结构化核对，请阅读原始返回"],
            "provenance": {"brain": "sciencepal_hcc", "agent_name": agent_name,
                           "files": files, "stages_ms": {"hcc": int(elapsed_ms)}},
            "disclaimer_zh": DISCLAIMER,
        }
        return visit

    population = _dim(report.get("population_check"), "Agent 未给出人群核查")
    timepoint = _dim(report.get("timepoint_check"), "Agent 未给出时点核查")
    input_dim = _input_dim(report)
    outcome = _dim(report.get("outcome_check"), "Agent 未给出结局核查")
    dims = [
        {"id": "population", **population},
        {"id": "timepoint", **timepoint},
        {"id": "input", **input_dim},
        {"id": "outcome", **outcome},
    ]

    ve = report.get("validation_evidence") if isinstance(report.get("validation_evidence"), dict) else {}
    strength = _strength(ve)
    limitations = [_s(x, 200) for x in _list(report.get("limitations")) if _s(x, 200)]
    cautions = limitations or ["Agent 未给出局限性说明，需人工复核"]
    summary = _s(report.get("overall_summary") or report.get("summary_zh"), 600) or "（Agent 未给综合结论）"
    if len(summary) < 8:
        summary = (summary + "；需人工复核").strip()

    expected = _expected(report)
    if not expected:
        # 从 input_check 的不可得变量推“还缺什么”，至少不空着
        ic = report.get("input_check") if isinstance(report.get("input_check"), dict) else {}
        for v in _list(ic.get("variables")):
            if isinstance(v, dict) and (v.get("available") is False or v.get("available_at_target") is False):
                name = _s(v.get("name"), 120)
                if name:
                    if len(name) < 2:
                        name = name + "（变量）"
                    expected.append({
                        "item_zh": name,
                        "why_zh": "模型所需变量在目标时点不可得",
                        "unlocks_zh": "补齐后可能从“无法确认”变成可讨论",
                        "cost_hint_zh": None,
                        "kind": "other",
                    })

    visit = {
        "schema_version": "0.1.0",
        "visit_id": "visit_%d_%s" % (int(time.time()), uuid.uuid4().hex[:6]),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "mode": MODE,
        "scenario": _scenario(scenario),
        "domain": {"status": "in_scope", "note_zh": "由 SciencePal HCC-Fit Agent 核查（HCC 专用，未做独立域闸门）"},
        "papers": papers,
        "matched": [{
            "paper_id": papers[0]["paper_id"],
            "dimensions": dims,
            "tier": _tier(dims, strength, report.get("overall_verdict")),
            "evidence_strength": strength,
        }],
        "integrated": {
            "summary_zh": summary,
            "options": [],
            "cautions_zh": cautions,
        },
        "expected_data": expected,
        "parse_mode": "structured",
        "raw_excerpts": [],
        "raw_answers": [{"stage": "hcc", "text": (raw_text or json.dumps(report, ensure_ascii=False))[:8000]}],
        "uncertainties": _uncertainties(dims, ve, limitations),
        "provenance": {"brain": "sciencepal_hcc", "agent_name": agent_name,
                       "files": files, "stages_ms": {"hcc": int(elapsed_ms)}},
        "disclaimer_zh": DISCLAIMER,
    }
    return visit
