"""SciencePal agent client for the balanced filter.

按官方文档（https://sciencepal.ai/help/sdk）的调用顺序：
    1. POST /api/agent/initiate（form：agent_id/prompt/stream/agent_select_type）
    2. GET  /api/agent-run/{run_id}/stream（SSE，data: <json>，[DONE] 结束；after_index=0 从头重放）
    3. GET  /api/agent-run/{run_id}（终态：completed / failed / stopped；asked = 等用户回答）

说明：
- 不再硬编码模型名；`SCIENCEPAL_MODEL` 为空时交给平台默认模型。
- 流读不到时回退：先查终态，再从 run 结构 / 重放流里取正文。
- 失败信息带 HTTP 语义（401/403/404/422/429），便于界面提示。
"""

from __future__ import annotations

import json
import time

import requests

from . import config


class SciencePalError(RuntimeError):
    pass


# 事件里这些类型不是最终回答：状态、工具调用/结果、路由、思考
SKIP_EVENT_TYPES = {
    "status", "tool_started", "tool_finished", "tool_call", "function_call",
    "tool_result", "thinking", "routing", "routing_decision", "model_resolved",
    "finishing", "run_progress_end", "thread_run_start",
    "assistant_response_start", "started",
}

TERMINAL_STATES = ("completed", "finished")
FAILED_STATES = ("failed", "error", "stopped", "cancelled")


def _base() -> str:
    base = (config.SCIENCEPAL_BASE_URL or "https://sciencepal.ai").rstrip("/")
    return base if base.endswith("/api") else base + "/api"


def _headers(api_key: str | None = None) -> dict:
    key = (api_key or config.SCIENCEPAL_API_KEY or "").strip()
    if not key:
        raise SciencePalError("缺少 SciencePal API Key（界面填写或 SCIENCEPAL_API_KEY）")
    return {"Authorization": "Bearer " + key, "Accept": "application/json"}


def _http_error(what: str, status: int, body: str) -> SciencePalError:
    detail = {
        401: "API Key 无效、过期或未授权",
        403: "没有该 Agent / 模型的权限，或密钥无权访问",
        404: "找不到 Agent 或接口（检查 SCIENCEPAL_FILTER_AGENT_ID 与 Base URL）",
        422: "参数不被接受（常见：模型在此账号不可用；不要手填模型名，留空用平台默认）",
        429: "额度用尽或被限流，请检查账户额度后重试",
    }.get(status, "HTTP %s" % status)
    return SciencePalError("%s失败（%s）：%s" % (what, detail, (body or "")[:300]))


def _state(obj: dict) -> str:
    return str((obj or {}).get("status") or (obj or {}).get("state")
               or (obj or {}).get("executionState") or "").lower()


def _event_text(obj) -> str:
    """从 SSE 事件里取“助手正文”；跳过状态/工具/路由噪音。"""
    if not isinstance(obj, dict):
        return ""
    st = str(obj.get("status_type") or obj.get("type") or "").lower()
    if st in SKIP_EVENT_TYPES:
        return ""
    content = obj.get("content")
    if isinstance(content, str) and content.strip().startswith("{"):
        try:
            inner = json.loads(content)
        except ValueError:
            inner = None
        if isinstance(inner, dict) and isinstance(inner.get("content"), str):
            content = inner["content"]
    if isinstance(content, dict) and isinstance(content.get("content"), str):
        content = content["content"]
    if isinstance(content, str) and content.strip():
        return content
    choices = obj.get("choices")
    if isinstance(choices, list) and choices:
        delta = choices[0].get("delta") or choices[0].get("message") or {}
        if isinstance(delta, dict) and isinstance(delta.get("content"), str):
            return delta["content"]
    for key in ("delta", "text", "answer", "output"):
        val = obj.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _stream_text(run_id: str, headers: dict, timeout_s: int, after: int = 0) -> str:
    """读 SSE；after_index=0 让平台从头重放（默认从尾部订阅，会漏掉已发生的回答）。"""
    url = "%s/agent-run/%s/stream" % (_base(), run_id)
    try:
        with requests.get(url, headers={**headers, "Accept": "text/event-stream"},
                          params={"after_index": after}, stream=True,
                          timeout=(10, max(20, int(timeout_s)))) as resp:
            if resp.status_code >= 400:
                return ""
            chunks: list[str] = []
            for raw in resp.iter_lines(decode_unicode=False):
                if not raw:
                    continue
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                if not payload:
                    continue
                try:
                    obj = json.loads(payload)
                except ValueError:
                    continue
                piece = _event_text(obj)
                if piece:
                    chunks.append(piece)
            return "".join(chunks).strip()
    except requests.RequestException:
        return ""


def _get_run(run_id: str, headers: dict) -> tuple[dict, str]:
    try:
        resp = requests.get("%s/agent-run/%s" % (_base(), run_id), headers=headers, timeout=20)
    except requests.RequestException as exc:
        raise SciencePalError("查询 Agent 运行状态失败：%s" % exc)
    if resp.status_code >= 400:
        raise _http_error("查询运行状态", resp.status_code, resp.text)
    try:
        body = resp.json()
    except ValueError:
        raise SciencePalError("运行状态返回不是 JSON：%s" % resp.text[:200])
    return body, _state(body)


def _wait_terminal(run_id: str, headers: dict, deadline: float) -> tuple[dict, str]:
    body, state = {}, ""
    while time.time() < deadline:
        body, state = _get_run(run_id, headers)
        if state in TERMINAL_STATES or state in FAILED_STATES or state == "asked":
            return body, state
        time.sleep(1.5)
    return body, state


def _answer_from_run(body: dict) -> str:
    """终态返回里可能直接带 messages/responses；挑助手正文。"""
    if not isinstance(body, dict):
        return ""
    for key in ("responses", "messages", "output", "result", "answer", "content"):
        val = body.get(key)
        items = val if isinstance(val, list) else [val]
        texts = []
        for item in items:
            if isinstance(item, dict):
                role = str(item.get("role") or item.get("author") or "").lower()
                if role and role not in ("assistant", "agent", "ai", "bot"):
                    continue
                st = str(item.get("status_type") or item.get("type") or "").lower()
                if st in SKIP_EVENT_TYPES:
                    continue
                text = _event_text(item)
            else:
                text = str(item or "")
            if text.strip():
                texts.append(text)
        joined = "\n".join(texts).strip()
        if joined:
            return joined
    return ""


def run_agent(agent_id: str, prompt: str, timeout_s: int = 120,
              web_search: bool = False, api_key: str | None = None) -> dict:
    """跑一次 Agent：initiate → 读流 → 确认终态。返回 answer/status/run_id/thread_id。"""
    t0 = time.perf_counter()
    headers = _headers(api_key)
    fields = {
        "agent_id": agent_id,
        "prompt": prompt,
        "stream": "true",
        "agent_select_type": "manual",
    }
    if web_search:
        fields["web_search_on"] = "true"
    model = (config.SCIENCEPAL_MODEL or "").strip()
    if model:                       # 官方要求用 available-models 返回的 id；留空=平台默认
        fields["model_name"] = model

    try:
        resp = requests.post("%s/agent/initiate" % _base(), headers=headers,
                             data=fields, timeout=(10, 30))
    except requests.RequestException as exc:
        raise SciencePalError("连不上 SciencePal（initiate）：%s" % exc)
    if resp.status_code >= 400:
        raise _http_error("initiate", resp.status_code, resp.text)
    try:
        init = resp.json()
    except ValueError:
        raise SciencePalError("initiate 返回不是 JSON：%s" % resp.text[:200])

    run_id = init.get("agent_run_id") or init.get("run_id") or init.get("id")
    thread_id = init.get("thread_id")
    if not run_id:
        raise SciencePalError("initiate 未返回 agent_run_id：%s"
                              % json.dumps(init, ensure_ascii=False)[:300])

    budget = max(15, int(timeout_s))
    deadline = time.time() + budget

    # 1) 官方时序：立刻订阅流（从头重放，避免漏掉已经过去的回答）
    text = _stream_text(run_id, headers, timeout_s=budget, after=0)

    # 2) 确认终态；asked = 等用户回答，不是完成
    body, state = _wait_terminal(run_id, headers, deadline)
    if state in FAILED_STATES:
        err = (body or {}).get("error") or (body or {}).get("detail") or state
        raise SciencePalError("Agent 运行失败（%s）：%s" % (state, str(err)[:300]))
    if state == "asked":
        raise SciencePalError(
            "Agent 在追问（asked），需要先回答才能继续；本服务不会自动回答。"
            "请改用不追问的 filter agent，或到 SciencePal 网页继续该会话（thread_id=%s, run_id=%s）"
            % (thread_id or "-", run_id))

    if not text:
        text = _answer_from_run(body)
    if not text:
        text = _stream_text(run_id, headers, timeout_s=30, after=0)   # 再重放一次
    if not text:
        raise SciencePalError(
            "运行已结束但流里没有正文（tokens_used 可能为 0）；"
            "run_id=%s，status=%s" % (run_id, state or "unknown"))

    return {
        "answer": text,
        "status": state or "completed",
        "run_id": run_id,
        "thread_id": thread_id,
        "latency_ms": round((time.perf_counter() - t0) * 1000),
    }


def filter_papers(scenario: dict, papers: list[dict], timeout_s: int = 90,
                  api_key: str | None = None) -> dict:
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
