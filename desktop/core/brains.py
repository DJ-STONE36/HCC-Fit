# -*- coding: utf-8 -*-
"""
brains.py — 三种"大脑"，同一条流水线可替换。

1) StubBrain   : 不联网、确定性演示（默认）。所有产出都标 demo，用于先把链路跑通。
2) ManualBrain : 把每一阶段的提示写到工作区，等人/别的窗口把 JSON 放回来（离线可用的"人工大脑"）。
3) SciencePalBrain（见 sciencepal.py）: 调平台的 Agent / 检索 / 沙盒。

契约：run_stage(stage, payload) -> dict（结构见 planner.STAGE_SPECS）。
"""

from __future__ import annotations

import hashlib
import json
import os
import time


class BrainError(RuntimeError):
    pass


class Pending(RuntimeError):
    """ManualBrain：还差人把结果放回来。"""


def _h(text: str) -> int:
    return int(hashlib.sha256(text.encode("utf-8")).hexdigest()[:8], 16)


def _title_from_name(name: str) -> str:
    base = os.path.splitext(os.path.basename(name))[0]
    cleaned = base.replace("_", " ").replace("-", " ").strip()
    return cleaned[:120] or "未命名论文"


class Brain:
    name = "base"
    available = False

    def __init__(self, workspace: str, **opts):
        self.workspace = workspace
        self.opts = opts

    def run_stage(self, stage: str, payload: dict) -> dict:  # pragma: no cover
        raise NotImplementedError


class StubBrain(Brain):
    """确定性演示大脑：输入相同 → 输出相同。永远不联网。"""

    name = "stub"
    available = True

    def run_stage(self, stage: str, payload: dict) -> dict:
        if stage == "domain":
            return {
                "status": "in_scope",
                "note_zh": "肝脏原发肿瘤（HCC）+ 与治疗决策相关 → 在域内（演示判定）",
            }
        if stage == "papers":
            files = [f for f in payload.get("files", []) if f.lower().endswith((".pdf", ".md", ".txt", ".docx"))]
            papers = []
            for i, f in enumerate(files):
                seed = _h(f) % 100
                decision = "include" if i < 3 else "exclude"
                reason = (
                    "报告了预测模型与区分度，且人群为 HCC（演示）"
                    if decision == "include"
                    else "演示：未报告区分度或人群不符，按规则排除"
                )
                papers.append({
                    "paper_id": "p%d" % (i + 1),
                    "title": _title_from_name(f),
                    "doi": None,
                    "year": 2020 + seed % 5,
                    "source": "uploaded",
                    "decision": decision,
                    "reason_zh": reason,
                    "file_path": os.path.join(self.workspace, "files", f),
                })
            if not papers:
                papers = [{
                    "paper_id": "p0",
                    "title": "演示：未上传论文，使用内置示例（TACE 后 OS 评分）",
                    "doi": None,
                    "year": 2022,
                    "source": "search",
                    "decision": "include",
                    "reason_zh": "演示：无上传文件时由内置样例占位",
                    "file_path": None,
                }]
            return {"papers": papers}

        if stage == "match":
            out = []
            for i, p in enumerate(payload.get("included", [])):
                mode = _h(p["paper_id"]) % 3
                if mode == 0:
                    dims = [
                        ("population", "match", "人群为 HBV 相关 HCC（演示）"),
                        ("timepoint", "match", "用于同一时点，窗口覆盖（演示）"),
                        ("input", "match", "所需变量在场景中可得（演示）"),
                        ("outcome", "match", "结局与时间窗一致（演示）"),
                    ]
                    tier, es = "applicable", "moderate"
                elif mode == 1:
                    dims = [
                        ("population", "match", "人群接近（演示）"),
                        ("timepoint", "match", "时点一致（演示）"),
                        ("input", "unknown", "需要影像组学特征，当前未产出（演示）"),
                        ("outcome", "match", "结局一致（演示）"),
                    ]
                    tier, es = "undetermined", "weak"
                else:
                    dims = [
                        ("population", "match", "人群接近（演示）"),
                        ("timepoint", "mismatch", "模型需术后病理，当前为术前决策（演示）"),
                        ("input", "mismatch", "必需变量不可得（演示）"),
                        ("outcome", "match", "结局一致（演示）"),
                    ]
                    tier, es = "not_applicable", "moderate"
                out.append({
                    "paper_id": p["paper_id"],
                    "dimensions": [{"id": d, "status": s, "reason_zh": r, "quote": None, "location": None} for d, s, r in dims],
                    "tier": tier,
                    "evidence_strength": es,
                })
            return {"matched": out}

        if stage == "integrate":
            matched = payload.get("matched", [])
            usable = [m for m in matched if m["tier"] in ("applicable", "conditional")]
            options = []
            for m in usable:
                options.append({
                    "name": "按 %s 的分层思路用于沟通" % m["paper_id"],
                    "basis_paper_ids": [m["paper_id"]],
                    "confidence": "moderate" if m["tier"] == "applicable" else "low",
                    "note_zh": "演示：仅用于风险沟通，不作为治疗选择依据",
                    "what_it_needs": [],
                })
            if not options:
                options.append({
                    "name": "先补齐关键数据，再做模型匹配（演示）",
                    "basis_paper_ids": [],
                    "confidence": "low",
                    "note_zh": "演示：当前没有可用模型，建议先补数据",
                    "what_it_needs": ["影像组学特征", "病理 MVI"],
                })
            return {
                "summary_zh": "演示综合：%d 篇纳入，%d 篇可用于讨论，其余卡在时点或输入上。" % (len(matched), len(usable)),
                "options": options,
                "cautions_zh": [
                    "演示数据，不是真实论文结论",
                    "所有模型结论均需人工复核",
                    "不构成诊断或治疗建议",
                ],
            }

        if stage == "expected":
            return {
                "expected_data": [
                    {"item_zh": "动脉期影像组学特征（4 项）", "why_zh": "有 DICOM，可补；补齐后能解锁影像组学模型", "unlocks_zh": "1 个候选从“需补数据”→“可讨论”", "cost_hint_zh": "约 1 次后处理", "kind": "imaging"},
                    {"item_zh": "病理 MVI 结果", "why_zh": "术后模型的核心输入；术前无法获得，属于时间泄漏", "unlocks_zh": "术后场景下可评估", "cost_hint_zh": "需手术标本", "kind": "pathology"},
                    {"item_zh": "AFP-L3 / PIVKA-II", "why_zh": "部分模型需要，当前未查", "unlocks_zh": "视具体模型而定", "cost_hint_zh": "一次抽血", "kind": "lab"},
                ]
            }
        raise BrainError("未知阶段：%s" % stage)


class ManualBrain(Brain):
    """离线人工大脑：写提示到工作区，读回 JSON。适合没有网络/没有 Key 时。"""

    name = "manual"
    available = True

    def run_stage(self, stage: str, payload: dict) -> dict:
        pdir = os.path.join(self.workspace, "manual")
        os.makedirs(pdir, exist_ok=True)
        ask = os.path.join(pdir, "%s.prompt.md" % stage)
        ans = os.path.join(pdir, "%s.json" % stage)
        if not os.path.exists(ask):
            with open(ask, "w", encoding="utf-8") as fh:
                fh.write("# 阶段：%s\n\n## 输入\n\n```json\n%s\n```\n\n"
                         "把结果写到 %s（结构见 planner.STAGE_SPECS）。\n" % (stage, json.dumps(payload, ensure_ascii=False, indent=2), ans))
        if not os.path.exists(ans):
            raise Pending("等待人工结果：%s" % ans)
        with open(ans, encoding="utf-8") as fh:
            return json.load(fh)


def build_brain(mode: str, workspace: str, **opts) -> Brain:
    mode = (mode or "stub").lower()
    if mode == "stub":
        return StubBrain(workspace, **opts)
    if mode == "manual":
        return ManualBrain(workspace, **opts)
    if mode == "sciencepal":
        from sciencepal import SciencePalBrain
        return SciencePalBrain(workspace, **opts)
    raise BrainError("未知 mode：%s" % mode)
