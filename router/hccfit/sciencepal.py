"""SciencePal agent client for the balanced filter."""

from __future__ import annotations

import json
import time

import requests

from . import config


class SciencePalError(RuntimeError):
    pass


def _headers(api_key: str | None = None):
    key = api_key or config.SCIENCEPAL_API_KEY
    if not key:
        raise SciencePalError("SCIENCEPAL_API_KEY missing (header or env)")
    return {"Authorization": "Bearer " + key}


def _parse(text: str):
    s = (text or "").strip()
    if "```" in s:
        s = s.split("```")[1]
        if s.startswith("json"):
            s = s[4:]
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j < 0:
        return None
    try:
        return json.loads(s[i:j + 1])
    except Exception:
        return None


def _stream_text(run_id: str, api_key: str | None = None) -> str:
    r = requests.get(f"{config.SCIENCEPAL_BASE_URL}/api/agent-run/{run_id}/stream",
                     headers=_headers(api_key), timeout=40)
    if r.status_code != 200:
        return ""
    chunks = []
    for block in r.text.replace("\r\n", "\n").split("\n\n"):
        data = [ln[5:].strip() for ln in block.splitlines() if ln.startswith("data:")]
        if not data:
            continue
        try:
            m = json.loads("\n".join(data))
        except Exception:
            continue
        if m.get("type") == "assistant":
            c = m.get("content")
            if isinstance(c, dict):
                chunks.append(c.get("content") or "")
            elif c:
                chunks.append(str(c))
    return "\n".join(x for x in chunks if x)


def run_agent(agent_id: str, prompt: str, timeout_s: int = 120, web_search: bool = False, api_key: str | None = None) -> dict:
    t0 = time.perf_counter()
    files = {
        "prompt": (None, prompt),
        "agent_id": (None, agent_id),
        "model_name": (None, config.SCIENCEPAL_MODEL),
        "stream": (None, "false"),
        "web_search_on": (None, "true" if web_search else "false"),
    }
    r = requests.post(f"{config.SCIENCEPAL_BASE_URL}/api/agent/initiate",
                      headers=_headers(api_key), files=files, timeout=40)
    if r.status_code >= 400:
        raise SciencePalError(f"initiate {r.status_code}: {r.text[:200]}")
    init = r.json()
    run_id = init.get("agent_run_id")
    deadline = time.time() + timeout_s
    status = {}
    while time.time() < deadline:
        time.sleep(1.5)
        rr = requests.get(f"{config.SCIENCEPAL_BASE_URL}/api/agent-run/{run_id}",
                          headers=_headers(api_key), timeout=20)
        if rr.status_code == 200:
            status = rr.json()
            if str(status.get("status", "")).lower() in ("completed", "failed", "stopped", "error"):
                break
    answer = _stream_text(run_id, api_key)
    return {"answer": answer, "status": status.get("status"), "run_id": run_id,
            "latency_ms": round((time.perf_counter() - t0) * 1000)}


def filter_papers(scenario: dict, papers: list[dict], timeout_s: int = 90, api_key: str | None = None) -> dict:
    cand = [{"pmid": p.get("pmid"), "title": p.get("title"), "year": p.get("year"),
             "journal": p.get("journal"), "abstract": (p.get("abstract_snippet") or "")[:700],
             "why": p.get("why", ""), "risk": p.get("risk", "")} for p in papers]
    prompt = (
        "你是 HCC 预测模型检索的医生式监督器。场景：\n" + (scenario.get("text") or "") +
        "\n\n候选：\n" + json.dumps(cand, ensure_ascii=False) +
        "\n\n按医生流程：真实预测/预后模型？结局、时间零点、人群/治疗是否匹配？"
        "fit 0-2；不匹配 drop；冲突保留 fit=1 标注；不要因标题有 nomogram 就保留。"
        "只输出严格 JSON："
        '{"keep":[{"pmid":"","fit":0,"reason":""}],"drop":[{"pmid":"","reason":""}]}'
    )
    agent_id = config.SCIENCEPAL_FILTER_AGENT_ID
    if not agent_id:
        raise SciencePalError(
            "SCIENCEPAL_FILTER_AGENT_ID not configured; balanced cannot run SciencePal filter")
    res = run_agent(agent_id, prompt, timeout_s=timeout_s, api_key=api_key)
    parsed = _parse(res.get("answer", ""))
    return {**res, "parsed": parsed}
