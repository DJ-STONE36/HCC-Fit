# -*- coding: utf-8 -*-
"""Simple Router 的可选前置检索客户端（方案 A）。

只负责：场景映射 → POST /v1/search → 候选论文。
不修改五阶段 S1，也不把摘要冒充全文。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from PySide6.QtCore import QObject, Signal

from app.input_normalization import InputSession


class RouterError(RuntimeError):
    pass


def _outcome_value(text: str) -> str | None:
    t = (text or "").lower()
    if "复发" in t or "rfs" in t or "dfs" in t:
        return "RFS"
    if "无进展" in t or "pfs" in t:
        return "PFS"
    if "肝衰" in t or "phlf" in t or "liver failure" in t:
        return "PHLF"
    if "反应" in t or "response" in t or "recist" in t:
        return "response"
    if "住院" in t or "los" in t:
        return "LOS"
    if "生存" in t or "os" in t:
        return "OS"
    return None


def _horizon_months(text: str) -> int | None:
    m = re.search(r"(\d+)\s*个月", text or "")
    if m:
        return max(0, min(120, int(m.group(1))))
    m = re.search(r"([一二两三四五六七八九十\d]+)\s*年", text or "")
    if not m:
        return None
    cn = {"一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}
    raw = m.group(1)
    years = int(raw) if raw.isdigit() else cn.get(raw, 0)
    return max(0, min(120, years * 12)) if years else None


def _time_zero(scenario: dict, session: InputSession) -> str | None:
    tp = str(scenario.get("use_timepoint") or "")
    if tp in ("pre_surgery", "post_surgery"):
        return "surgery"
    if tp == "pre_treatment":
        return "tace"  # 仅作为检索起点；具体治疗由 treatment_state 表达
    t = session.fields.get("treatment")
    if t and t.status == "normalized":
        n = t.normalized or ""
        if "TACE" in n:
            return "tace"
        if "系统" in n:
            return "systemic"
        if "移植" in n:
            return "transplant"
        if "切除" in n:
            return "surgery"
    return None


def _treatment_state(session: InputSession) -> str | None:
    t = session.fields.get("treatment")
    if not t or t.status == "unknown":
        return None
    n = t.normalized or ""
    if "切除" in n:
        return "curative resection"
    if "TACE" in n:
        return "TACE"
    if "消融" in n:
        return "TACE"  # Router 只区分治疗大类；消融按局部治疗检索
    if "系统" in n:
        return "unresectable systemic"
    if "移植" in n:
        return "transplant"
    if "未治疗" in n:
        return "unspecified"
    return None


def from_session(session: InputSession, scenario: dict | None = None) -> dict:
    """把已确认的输入会话映射成 Router 的 scenario。"""
    if not session:
        raise RouterError("还没有确认的场景卡")
    d = session.fields.get("disease")
    if not d or d.status != "normalized" or "HCC" not in (d.normalized or ""):
        raise RouterError("Router 只接受已确认 HCC；当前疾病不是已确认 HCC")

    text = (session.raw_text or "").strip()
    if not text:
        sc = scenario or {}
        text = "；".join(x for x in (sc.get("patient_summary_zh"), sc.get("task_zh")) if x)
    out = {"text": text[:4000], "disease": "HCC"}
    tz = _time_zero(scenario or {}, session)
    if tz:
        out["time_zero"] = tz
    ts = _treatment_state(session)
    if ts:
        out["treatment_state"] = ts
    sc = scenario or {}
    ov = _outcome_value(str(sc.get("outcome_type") or "")) or \
        _outcome_value(str(session.fields.get("outcome").normalized or ""))
    if ov:
        out["outcome"] = ov
    hm = _horizon_months(str(sc.get("outcome_type") or "")) or \
        _horizon_months(str(session.fields.get("horizon").normalized or ""))
    if hm:
        out["horizon_months"] = hm
    return out


class RouterClient:
    def __init__(self, base_url: str, token: str = "", timeout: int = 160,
                 sciencepal_key: str = ""):
        self.base_url = (base_url or "").rstrip("/")
        self.token = (token or "").strip()
        self.sciencepal_key = (sciencepal_key or "").strip()
        self.timeout = timeout

    def _post(self, payload: dict) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if self.sciencepal_key:
            headers["X-SciencePal-API-Key"] = self.sciencepal_key
        req = urllib.request.Request(
            self.base_url + "/v1/search",
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")[:400]
            raise RouterError("Router HTTP %s: %s" % (exc.code, body))
        except Exception as exc:  # noqa: BLE001
            raise RouterError("%s: %s" % (type(exc).__name__, exc))

    def search(self, scenario: dict, strategy: str = "fast", k: int = 8) -> dict:
        payload = {
            "scenario": scenario,
            "strategy": strategy,
            "k": max(1, min(8, int(k))),
            "deadline_ms": 100000 if strategy == "auto" else 150000,
            "require_fulltext": False,
        }
        return self._post(payload)


class RouterWorker(QObject):
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, base_url: str, token: str, scenario: dict,
                 strategy: str, k: int, sciencepal_key: str = ""):
        super().__init__()
        self.base_url = base_url
        self.token = token
        self.scenario = scenario
        self.strategy = strategy
        self.k = k
        self.sciencepal_key = sciencepal_key

    def run(self):
        try:
            self.done.emit(RouterClient(self.base_url, self.token,
                                        sciencepal_key=self.sciencepal_key)
                           .search(self.scenario, self.strategy, self.k))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))
