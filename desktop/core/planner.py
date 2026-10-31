# -*- coding: utf-8 -*-
"""
planner.py — 把"就诊流程"拆成五个可观察阶段，串起大脑、场景与产物。

S0 域判定 → S1 论文选择 → S2 逐篇匹配 → S3 综合方案 → S4 期望补充数据 → visit.json

设计要点：
- 每个阶段都可被替换（sciencepal / stub），流水线本身不变；
- 每阶段发出事件，前端实时看到进度与失败原因；
- 判不了就带 unknown 往下走，绝不因为缺数据而中断（这是产品要求：数据不够也要给东西）。
"""

from __future__ import annotations

import json
import os
import time
import uuid

from brains import BrainError, Pending

DISCLAIMER = ("本结果仅用于临床决策前的证据准备，不构成诊断，不输出个体化概率，"
              "不替代多学科讨论与主治医师判断。")

STAGE_SPECS = {
    "domain": {"status": ["in_scope", "outside_scope", "unclear"], "note_zh": "str"},
    "papers": {"papers": "[{paper_id,title,doi,year,source,decision,reason_zh,file_path}]"},
    "match": {"matched": "[{paper_id,dimensions:[{id,status,reason_zh,quote,location}],tier,evidence_strength}]"},
    "integrate": {"summary_zh": "str", "options": "[{name,basis_paper_ids,confidence,note_zh,what_it_needs}]", "cautions_zh": "[str]"},
    "expected": {"expected_data": "[{item_zh,why_zh,unlocks_zh,cost_hint_zh,kind}]"},
}

STAGE_LABEL = {
    "domain": "S0 域判定",
    "papers": "S1 论文选择",
    "match": "S2 逐篇匹配",
    "integrate": "S3 综合方案",
    "expected": "S4 期望数据",
}


def _split(text):
    if isinstance(text, (list, tuple)):
        return [str(p).strip() for p in text if str(p).strip()]
    if not text:
        return []
    for sep in ("，", ",", "、", ";", "；", "\n"):
        text = text.replace(sep, "|")
    return [p.strip() for p in text.split("|") if p.strip()]


class Planner:
    def __init__(self, brain, workspace: str, scenario: dict, emit=None, files=None,
                 split_papers: bool = False, attachments=None):
        self.brain = brain
        self.workspace = workspace
        self.scenario = scenario
        self.emit = emit or (lambda ev: None)
        self.files = files or []
        self.attachments = [a for a in (attachments or []) if str(a).strip()]  # 报告后补充的患者资料
        self.split_papers = bool(split_papers)   # 多篇时逐篇发给 Agent（S2 单独调用）
        self.timing = {}
        self.saved_path = None
        self.text_fallbacks = []
        self.raw_answers = []
        self.degraded_note = False

    # ---------- helpers ----------
    def _stage(self, key, payload, label=None, emit_key=None):
        emit_key = emit_key or key
        label = label or STAGE_LABEL.get(key, key)
        t0 = time.time()
        self.emit({"stage": emit_key, "label": label, "status": "start"})
        try:
            out = self.brain.run_stage(key, payload)
        except Pending as exc:
            self.emit({"stage": emit_key, "label": label, "status": "pending", "detail": str(exc)})
            raise
        except BrainError as exc:
            self.emit({"stage": emit_key, "label": label, "status": "error", "detail": str(exc)})
            raise
        if getattr(self.brain, "degraded_files", False) and not self.degraded_note:
            self.degraded_note = True
            self.emit({"stage": emit_key, "label": label, "status": "degraded",
                       "detail": "平台未能解析附件，已改用抽出的文字"})
        raw = getattr(self.brain, "last_text", None)
        if raw:
            self.raw_answers.append({"stage": key, "text": str(raw)[:1500]})
            self.brain.last_text = None
        ms = int((time.time() - t0) * 1000)
        self.timing[key] = self.timing.get(key, 0) + ms
        out = self._degrade(key, out, emit_key=emit_key, label=label)
        self.emit({"stage": emit_key, "label": label, "status": "ok", "ms": ms,
                   "detail": self._brief(key, out)})
        return out

    def _degrade(self, key, out, emit_key=None, label=None):
        """大脑没按 JSON 返回时，降级成"能继续跑"的结构，而不是让整条链路失败。"""
        emit_key = emit_key or key
        label = label or STAGE_LABEL.get(key, key)
        if not (isinstance(out, dict) and out.get("_parse") == "text"):
            return out
        text = (out.get("_text") or "").strip()
        self.text_fallbacks.append({"stage": key, "text": text[:1200]})
        self.emit({"stage": emit_key, "label": label, "status": "degraded",
                   "detail": "Agent 未按 JSON 返回，已降级为原文摘要"})
        if key == "domain":
            status = "unclear"
            for kw, val in (("不匹配", "outside_scope"), ("无法确认", "unclear"), ("匹配", "in_scope")):
                if kw in text:
                    status = val
                    break
            return {"status": status, "note_zh": (text[:280] or "Agent 未按 JSON 返回，需人工确认")}
        if key == "papers":
            return {"papers": []}
        if key == "match":
            return {"matched": []}
        if key == "integrate":
            return {"summary_zh": text[:600] or "Agent 未按 JSON 返回",
                    "options": [],
                    "cautions_zh": ["Agent 未按结构返回，以下为原文摘要，请人工判读"]}
        if key == "expected":
            return {"expected_data": []}
        return out

    @staticmethod
    def _brief(key, out):
        if key == "papers":
            ps = out.get("papers", [])
            inc = sum(1 for p in ps if p.get("decision") == "include")
            return "纳入 %d / 共 %d 篇" % (inc, len(ps))
        if key == "match":
            ms = out.get("matched", [])
            good = sum(1 for m in ms if m.get("tier") in ("applicable", "conditional"))
            return "%d 篇完成匹配，%d 篇可讨论" % (len(ms), good)
        if key == "integrate":
            return "%d 个方案" % len(out.get("options", []))
        if key == "expected":
            return "%d 项待补数据" % len(out.get("expected_data", []))
        return ""

    def _paper_file(self, paper: dict, index: int) -> str:
        """把 S1 的论文映射回 files 里的文件名（优先 file_path 的 basename）。"""
        fp = str((paper or {}).get("file_path") or "").strip()
        name = os.path.basename(fp) if fp else ""
        if name and name in self.files:
            return name
        if 0 <= index < len(self.files):
            return self.files[index]
        return ""

    # ---------- 主流程 ----------
    def run(self):
        base = {"scenario": self.scenario, "files": self.files,
                "attachments": self.attachments}

        domain = self._stage("domain", base)

        papers_out = self._stage("papers", dict(base, domain=domain))
        papers = papers_out.get("papers", [])
        included = [p for p in papers if p.get("decision") == "include"]

        if self.split_papers and len(included) > 1:
            matched = []
            for i, one in enumerate(included):
                fname = self._paper_file(one, i)
                payload = dict(base, included=[one], papers=[one],
                               files=([fname] if fname else self.files),
                               batch="%d/%d" % (i + 1, len(included)))
                label = "S2 逐篇匹配 · %d/%d %s" % (i + 1, len(included), fname or "")
                out = self._stage("match", payload, label=label, emit_key="match#%d" % i)
                matched.extend(out.get("matched") or [])
        else:
            matched_out = self._stage("match", dict(base, included=included, papers=papers))
            matched = matched_out.get("matched", [])

        integrated = self._stage("integrate", dict(base, matched=matched, papers=included))

        expected = self._stage("expected", dict(base, matched=matched, integrated=integrated))

        uncertainties = self._uncertainties(papers, matched, self.scenario)

        visit = {
            "schema_version": "0.1.0",
            "visit_id": "visit_%d_%s" % (int(time.time()), uuid.uuid4().hex[:6]),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "mode": getattr(self.brain, "mode_label", None) or getattr(self.brain, "name", "stub"),
            "scenario": {
                "task_zh": self.scenario.get("task_zh") or "未填写",
                "patient_summary_zh": self.scenario.get("patient_summary_zh") or "未填写",
                "use_timepoint": self.scenario.get("use_timepoint") or "未指定",
                "outcome_type": self.scenario.get("outcome_type") or "未指定",
                "inputs_given": _split(self.scenario.get("inputs_given")),
                "inputs_missing": _split(self.scenario.get("inputs_missing")),
            },
            "domain": {
                "status": domain.get("status", "unclear"),
                "note_zh": domain.get("note_zh", ""),
            },
            "papers": papers,
            "matched": matched,
            "integrated": {
                "summary_zh": integrated.get("summary_zh", ""),
                "options": integrated.get("options", []),
                "cautions_zh": integrated.get("cautions_zh", []),
            },
            "expected_data": expected.get("expected_data", []),
            "parse_mode": "text_fallback" if self.text_fallbacks else "structured",
            "raw_excerpts": self.text_fallbacks,
            "raw_answers": self.raw_answers,
            "uncertainties": uncertainties,
            "provenance": {
                "brain": getattr(self.brain, "name", None),
                "agent_name": self.brain.opts.get("agent_name") if hasattr(self.brain, "opts") else None,
                "thread_id": getattr(self.brain, "thread_id", None),
                "workspace": self.workspace,
                "files": self.files,
                "attachments": self.attachments,
                "stages_ms": self.timing,
            },
            "disclaimer_zh": DISCLAIMER,
        }
        self._save(visit)
        return visit

    @staticmethod
    def _uncertainties(papers, matched, scenario):
        out = []
        for m in matched:
            for d in m.get("dimensions", []):
                if d.get("status") == "unknown":
                    out.append("论文 %s 的「%s」无法确认：%s" % (m.get("paper_id"), d.get("id"), d.get("reason_zh", "")))
        if not papers:
            out.append("没有检索/上传到任何论文，本次结论仅基于场景本身。")
        if not (scenario.get("outcome_type") or "").strip():
            out.append("未指定要回答的结局（OS/RFS/复发等），匹配可能偏离。")
        if len(out) < 1:
            out.append("本次未记录到显式不确定项，但仍需人工复核论文结论与适用边界。")
        return out[:8]

    def _save(self, visit):
        runs = os.path.join(self.workspace, "runs")
        os.makedirs(runs, exist_ok=True)
        path = os.path.join(runs, visit["visit_id"] + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(visit, fh, ensure_ascii=False, indent=2)
        self.saved_path = path
        return path


def _merge_hcc_visits(visits) -> dict:
    """把“逐篇核查”的多份 visit 合并成一份：papers/matched 叠加，摘要分段，期望数据去重。"""
    base = dict(visits[0])
    base["visit_id"] = "visit_%d_%s" % (int(time.time()), uuid.uuid4().hex[:6])
    base["papers"] = [p for v in visits for p in (v.get("papers") or [])]
    base["matched"] = [m for v in visits for m in (v.get("matched") or [])]

    parts = []
    for v in visits:
        title = ""
        ps = v.get("papers") or []
        if ps:
            title = ps[0].get("title") or ps[0].get("file_path") or ""
        summary = (v.get("integrated") or {}).get("summary_zh") or ""
        if summary.strip():
            parts.append(("【%s】%s" % (title, summary)) if title else summary)
    integrated = dict(base.get("integrated") or {})
    integrated["summary_zh"] = "\n\n".join(parts)

    def _uniq(items, key):
        seen, out = set(), []
        for it in items:
            k = key(it)
            if k in seen:
                continue
            seen.add(k)
            out.append(it)
        return out

    integrated["cautions_zh"] = _uniq(
        [c for v in visits for c in (v.get("integrated") or {}).get("cautions_zh") or []],
        lambda c: str(c))
    base["integrated"] = integrated
    base["expected_data"] = _uniq(
        [e for v in visits for e in (v.get("expected_data") or [])],
        lambda e: (e.get("item_zh"), e.get("kind")))
    base["uncertainties"] = _uniq(
        [u for v in visits for u in (v.get("uncertainties") or [])], lambda u: str(u))[:12]
    base["raw_answers"] = [a for v in visits for a in (v.get("raw_answers") or [])]
    base["raw_excerpts"] = [a for v in visits for a in (v.get("raw_excerpts") or [])]
    base["parse_mode"] = ("structured"
                          if all(v.get("parse_mode") == "structured" for v in visits)
                          else "text_fallback")
    prov = dict(base.get("provenance") or {})
    prov["files"] = [f for v in visits for f in (v.get("provenance") or {}).get("files") or []]
    prov["stages_ms"] = {"hcc": sum(int((v.get("provenance") or {}).get("stages_ms", {})
                                       .get("hcc", 0)) for v in visits)}
    base["provenance"] = prov
    return base


class HccPlanner:
    """SciencePal HCC-Fit Agent 的单次核查：一条 prompt → 一份报告 → visit.json。

    split_papers=True 且有多篇时：逐篇调用 Agent，再把 matched/papers/期望数据合并成一份 visit。
    """

    LABEL = "HCC-Fit 单次核查"

    def __init__(self, brain, workspace: str, scenario: dict, emit=None, files=None,
                 split_papers: bool = False, attachments=None):
        self.brain = brain
        self.workspace = workspace
        self.scenario = scenario or {}
        self.emit = emit or (lambda ev: None)
        self.files = files or []
        self.attachments = [a for a in (attachments or []) if str(a).strip()]
        self.split_papers = bool(split_papers)
        self.saved_path = None

    def _paper_text(self, files=None):
        files = list(files if files is not None else self.files)
        text = (self.scenario.get("paper_text") or "").strip()
        if text or not files:
            return text
        try:                                   # 用户只传了 PDF：抠出文字兜底
            from pdftext import extract
            path = os.path.join(self.workspace, "files", files[0])
            text, note = extract(path)
            return text or ""
        except Exception:
            return ""

    def run(self):
        self.emit({"stage": "hcc", "label": self.LABEL, "status": "start"})

        if self.split_papers and len(self.files) > 1:
            visits = []
            for i, name in enumerate(self.files):
                key = "hcc#%d" % i
                label = "单次核查 · %d/%d %s" % (i + 1, len(self.files), name)
                self.emit({"stage": key, "label": label, "status": "start"})
                t_one = time.time()
                try:
                    one_visit = self._run_one([name])
                except Exception as exc:  # noqa: BLE001
                    self.emit({"stage": key, "label": label, "status": "error",
                               "detail": str(exc)[:300]})
                    raise
                visits.append(one_visit)
                matched_one = one_visit.get("matched") or []
                self.emit({"stage": key, "label": label, "status": "ok",
                           "ms": int((time.time() - t_one) * 1000),
                           "detail": ("tier=%s" % matched_one[0]["tier"]) if matched_one else "原文降级"})
            visit = _merge_hcc_visits(visits)
            self._save(visit)
            matched = visit.get("matched") or []
            total = sum(int((v.get("provenance") or {}).get("stages_ms", {}).get("hcc", 0))
                        for v in visits)
            self.emit({"stage": "hcc", "label": self.LABEL, "status": "ok", "ms": total,
                       "detail": "逐篇完成 %d 篇%s" % (
                           len(visits),
                           ("，首篇 tier=%s" % matched[0]["tier"]) if matched else "")})
            if visit.get("parse_mode") == "text_fallback":
                self.emit({"stage": "hcc", "label": self.LABEL, "status": "degraded",
                           "detail": "部分论文 Agent 未按 JSON 返回，已展示原文"})
            return visit

        visit = self._run_one(self.files[:1])
        matched = visit.get("matched") or []
        detail = ("tier=%s" % matched[0]["tier"]) if matched else "原文降级"
        if any(a.get("stage") == "hcc_original" for a in visit.get("raw_answers") or []):
            detail += "（二次 JSON 修复）"
        self.emit({"stage": "hcc", "label": self.LABEL, "status": "ok",
                   "ms": int((visit.get("provenance") or {}).get("stages_ms", {}).get("hcc", 0)),
                   "detail": detail})
        if visit.get("parse_mode") == "text_fallback":
            self.emit({"stage": "hcc", "label": self.LABEL, "status": "degraded",
                       "detail": "Agent 未按 JSON 返回，已展示原文"})
        return visit

    def _run_one(self, one):
        """对一份（或一组）论文跑一次单次核查，返回 visit（不落盘）。"""
        from hcc_report import build_prompt, normalize_report

        t0 = time.time()
        try:
            prompt = build_prompt(self.scenario, self._paper_text(one), one,
                                  attachments=self.attachments)
            abs_files = [os.path.join(self.workspace, "files", f) for f in one]
            abs_files += [os.path.join(self.workspace, "files", a) for a in self.attachments]
            out = self.brain.run_hcc_report(prompt, abs_files)
        except BrainError as exc:
            self.emit({"stage": "hcc", "label": self.LABEL, "status": "error", "detail": str(exc)})
            raise
        except Exception as exc:  # noqa: BLE001
            detail = "%s: %s" % (type(exc).__name__, exc)
            self.emit({"stage": "hcc", "label": self.LABEL, "status": "error", "detail": detail})
            raise BrainError(detail)

        # 第一次只回了 Markdown/分析：让它把同一结论再输出一次纯 JSON
        original_text = ""
        repaired = False
        if isinstance(out, dict) and out.get("_parse") == "text":
            original_text = out.get("_text") or ""
            try:
                fixed = self.brain.repair_hcc_json(original_text)
            except Exception:
                fixed = None
            if isinstance(fixed, dict) and fixed.get("_parse") != "text":
                out = fixed
                repaired = True
            self.emit({"stage": "hcc", "label": self.LABEL, "status": "degraded",
                       "detail": "Agent 首次未按 JSON 返回，已发起二次 JSON 修复"})

        ms = int((time.time() - t0) * 1000)
        raw = getattr(self.brain, "last_text", None) or ""
        if not repaired and original_text:
            raw = original_text
        if not raw:
            if isinstance(out, dict) and out.get("_parse") == "text":
                raw = out.get("_text") or ""
            else:
                raw = json.dumps(out, ensure_ascii=False)
        visit = normalize_report(out, self.scenario, one, self.workspace, raw, ms,
                                 agent_name=getattr(self.brain, "agent_name", None))
        visit.setdefault("provenance", {})["attachments"] = list(self.attachments)
        visit["provenance"]["thread_id"] = getattr(self.brain, "thread_id", None)
        visit["provenance"]["workspace"] = self.workspace
        if repaired and original_text:
            visit["raw_answers"].insert(0, {"stage": "hcc_original", "text": original_text[:8000]})
        return visit

    def _save(self, visit):
        runs = os.path.join(self.workspace, "runs")
        os.makedirs(runs, exist_ok=True)
        path = os.path.join(runs, visit["visit_id"] + ".json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(visit, fh, ensure_ascii=False, indent=2)
        self.saved_path = path
        return path
