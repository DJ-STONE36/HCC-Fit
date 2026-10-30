# -*- coding: utf-8 -*-
"""报告后补充资料：只追加用户原文，全量重跑；不改 Agent v0.6。"""
from __future__ import annotations

import copy
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class SupplementItem:
    item_zh: str
    why_zh: str = ""
    unlocks_zh: str = ""
    kind: str | None = None
    checked: bool = False
    value: str = ""


@dataclass
class Supplement:
    supplement_id: str
    parent_visit_id: str
    parent_input_id: str
    raw_text: str
    expected_items: list[SupplementItem] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    applied: bool = False
    new_visit_id: str | None = None

    @classmethod
    def from_visit(cls, visit: dict, raw_text: str) -> "Supplement":
        items = []
        for e in visit.get("expected_data") or []:
            items.append(SupplementItem(
                item_zh=str(e.get("item_zh") or ""),
                why_zh=str(e.get("why_zh") or ""),
                unlocks_zh=str(e.get("unlocks_zh") or ""),
                kind=e.get("kind"),
            ))
        return cls(
            supplement_id="sup_" + time.strftime("%Y%m%d") + "_" + uuid.uuid4().hex[:8],
            parent_visit_id=str(visit.get("visit_id") or ""),
            parent_input_id=str((visit.get("provenance") or {}).get("input_id") or ""),
            raw_text=(raw_text or "").strip()[:4000],
            expected_items=items,
        )

    def to_dict(self):
        return asdict(self)

    def save(self, path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path):
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        items = [SupplementItem(**x) for x in data.pop("expected_items", [])]
        obj = cls(**data)
        obj.expected_items = items
        return obj


def _text(value) -> str:
    """兼容 visit.json 的数组字段和旧版字符串字段。"""
    if isinstance(value, (list, tuple)):
        return "、".join(str(x).strip() for x in value if str(x).strip())
    return str(value or "").strip()


def build_supplemented_scenario(visit: dict, raw_text: str) -> dict:
    """原场景 + 补充原文；不自动解释、不伪造已补齐。"""
    sc = copy.deepcopy(visit.get("scenario") or {})
    add = (raw_text or "").strip()[:4000]
    if not add:
        return sc
    patient = _text(sc.get("patient_summary_zh"))
    sc["patient_summary_zh"] = (patient + "\n\n【报告后补充资料】\n" + add).strip()
    given = _text(sc.get("inputs_given"))
    sc["inputs_given"] = (given + ("；" if given else "") + "报告后补充：" + add).strip()
    # visit.json 里 inputs_missing 是数组；Planner._split 需要字符串或数组，这里统一成字符串
    sc["inputs_missing"] = _text(sc.get("inputs_missing"))
    # 是否补齐由下一次 Agent 判断，不在这里假装补上
    return sc
