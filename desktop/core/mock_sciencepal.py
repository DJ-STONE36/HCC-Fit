# -*- coding: utf-8 -*-
"""
mock_sciencepal.py — 本地"假平台"，用来在没有 Key / 不联网时验证适配层。

刻意复刻真实平台的三条行为（都已在真机上撞到过）：
  1) POST /api/agent/initiate  = 建会话 **并且立刻起一个任务**（所以随后调 agent/start 会 409）
  2) POST /api/thread/{id}/agent/start = 追问；会话里已有活跃任务时返回 409
  3) GET  /api/agent-run/{id} = 先 running 两次，再 completed，最终答案在 messages[].content 里的 ```json 块

    python tools/mock_sciencepal.py --port 8899      # 单独跑，做手工调试
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from urllib.parse import parse_qs, urlparse
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ANSWER = {
    "status": "in_scope",
    "note_zh": "肝脏原发肿瘤（HCC）+ 与治疗决策相关（mock 判定）",
    "papers": [{
        "paper_id": "p1", "title": "Mock 2024 · very early recurrence after resection",
        "doi": "10.0000/mock.0001", "year": 2024, "source": "uploaded",
        "decision": "include", "reason_zh": "报告了预测模型与区分度，人群为 HCC（mock）",
    }],
    "matched": [{
        "paper_id": "p1",
        "dimensions": [
            {"id": "population", "status": "match", "reason_zh": "人群为 HBV 相关 HCC（mock）", "quote": "HCC patients", "location": "abstract"},
            {"id": "timepoint", "status": "match", "reason_zh": "术前使用、12 个月窗口（mock）", "quote": "before surgery", "location": "methods"},
            {"id": "input", "status": "unknown", "reason_zh": "需要 4 项影像组学特征，当前未产出（mock）", "quote": "radiomics", "location": "methods"},
            {"id": "outcome", "status": "match", "reason_zh": "结局为 1 年内复发（mock）", "quote": "early recurrence", "location": "abstract"},
        ],
        "tier": "undetermined", "evidence_strength": "weak",
    }],
    "summary_zh": "mock：1 篇纳入，条件基本匹配但缺影像组学，暂不能给可用结论。",
    "options": [{
        "name": "补影像组学后再评估（mock）", "basis_paper_ids": ["p1"],
        "confidence": "low", "note_zh": "mock：仅用于流程演示", "what_it_needs": ["影像组学特征"],
    }],
    "cautions_zh": ["mock 数据，不是真实论文结论", "需人工复核"],
    "expected_data": [{
        "item_zh": "动脉期影像组学特征（4 项）", "why_zh": "补齐后可评估该模型（mock）",
        "unlocks_zh": "1 个候选从「需补数据」→「可讨论」", "cost_hint_zh": "1 次后处理", "kind": "imaging",
    }],
}

STATE = {"threads": {}, "runs": {}, "delay": 1.5, "hold": False, "text_mode": False, "messages_broken": False, "no_agents": False, "prepare_fail_once": False, "failed_runs": set()}
LOCK = threading.Lock()


def _new_run(thread_id):
    rid = "run_" + uuid.uuid4().hex[:6]
    STATE["runs"][rid] = {"id": rid, "thread_id": thread_id, "started_at": time.time()}
    STATE["threads"].setdefault(thread_id, {"id": thread_id, "runs": []})["runs"].append(rid)
    return rid


def _status(run):
    """按时间推进状态：更像真的平台，也让"等它空下来"这段逻辑可测。"""
    if STATE["hold"]:
        return "running"
    return "completed" if time.time() - run["started_at"] >= STATE["delay"] else "running"


def reset(delay=1.5, hold=False, text_mode=False, messages_broken=False, no_agents=False,
          prepare_fail_once=False):
    with LOCK:
        STATE["threads"].clear()
        STATE["runs"].clear()
        STATE["delay"] = delay
        STATE["hold"] = hold
        STATE["text_mode"] = text_mode
        STATE["messages_broken"] = messages_broken
        STATE["no_agents"] = no_agents
        STATE["prepare_fail_once"] = prepare_fail_once
        STATE["failed_runs"] = set()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        return

    def _send(self, code, obj):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _answer_text():
        if STATE["text_mode"]:
            return "结论：该场景在范围内，但缺少影像组学。\n（纯文本返回，用于验证降级）"
        return "结论：在范围内。\n\n\u0060\u0060\u0060json\n" + json.dumps(ANSWER, ensure_ascii=False) + "\n\u0060\u0060\u0060"

    def _stream(self, rid):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.end_headers()
        qs = parse_qs(urlparse(self.path).query)
        after = (qs.get("after_index") or [None])[0]
        if after is None:
            # 复刻真机：不带 after_index 只能看到收尾事件，答案早就冲过去了
            events = [
                {"status_type": "model_resolved", "model_name": "deepseek/deepseek-v4-flash"},
                {"status_type": "finishing", "phase": "persistence", "message": "Saving workspace..."},
                {"status_type": "run_progress_end", "state": "completed", "tokens_used": 0},
            ]
        else:
            answer = self._answer_text()
            events = [{"message_id": "m0", "type": "status", "is_llm_message": False,
                       "content": json.dumps({"status_type": "thread_run_start"})},
                      {"message_id": "m1", "type": "status", "is_llm_message": False,
                       "content": json.dumps({"status_type": "assistant_response_start"})}]
            step = 40
            for i in range(0, len(answer), step):
                events.append({"message_id": None, "type": "assistant", "is_llm_message": True,
                               "content": json.dumps({"role": "assistant",
                                                      "content": answer[i:i + step]}, ensure_ascii=False),
                               "metadata": json.dumps({"stream_status": "chunk"})})
            events.append({"message_id": "m9", "type": "status", "is_llm_message": False,
                           "content": json.dumps({"status_type": "run_progress_end",
                                                  "state": "completed"})})
        for ev in events:
            self.wfile.write(("data: " + json.dumps(ev, ensure_ascii=False) + "\n\n").encode("utf-8"))
            self.wfile.flush()

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n) if n else b""

    def do_GET(self):
        path = self.path.split("?")[0]
        m_msg = re.match(r"^/api/thread/([^/]+)/messages", path)
        if m_msg:
            return self._send(405, {"detail": "Method Not Allowed"})
        m_stream = re.match(r"^/api/agent-run/([^/]+)/stream", path)
        if m_stream:
            return self._stream(m_stream.group(1))
        if path == "/api/agents":
            if STATE.get("no_agents"):
                return self._send(200, {"agents": []})
            return self._send(200, {"agents": [{"id": "ag_hccfit", "name": "HCC-Fit"}]})
        m = re.match(r"^/api/thread/([^/]+)/agent-runs$", path)
        if m:
            tid = m.group(1)
            runs = [STATE["runs"][r] for r in STATE["threads"].get(tid, {}).get("runs", [])]
            return self._send(200, {"agent_runs": [{"id": r["id"], "status": _status(r)} for r in runs]})
        m = re.match(r"^/api/agent-run/([^/]+)$", path)
        if m:
            rid = m.group(1)
            run = STATE["runs"].get(rid)
            if not run:
                return self._send(404, {"detail": "run not found"})
            if rid in STATE.get("failed_runs", set()):
                return self._send(200, {
                    "id": rid, "status": "failed",
                    "error": '{"code":"workspace_context_prepare_failed",'
                             '"message":"Could not prepare the selected files."}',
                })
            state = _status(run)
            if state == "completed":
                if STATE["text_mode"]:
                    content = ("结论：该场景在范围内，但缺少影像组学，暂不能给可用结论。\n"
                               "（这是纯文本返回，用来验证降级路径）")
                else:
                    content = "结果如下：\n```json\n" + json.dumps(ANSWER, ensure_ascii=False) + "\n```"
                return self._send(200, {
                    "id": rid, "status": "completed",
                    "messages": [{"role": "assistant", "content": content}],
                })
            return self._send(200, {"id": rid, "status": state})
        return self._send(404, {"detail": "not found"})

    def do_POST(self):
        path = self.path.split("?")[0]
        raw = self._body()
        m_msg = re.match(r"^/api/thread/([^/]+)/messages", path)
        if m_msg:
            if STATE["messages_broken"]:
                return self._send(500, {"detail": "messages endpoint broke"})
            return self._send(200, {"messages": [
                {"role": "assistant", "status_type": "tool_started",
                 "function_name": "auto_judge_agent", "message": "u6b63"},
                {"role": "assistant", "content": "<function_calls><invoke name=\"auto_judge_agent\"></invoke></function_calls>"},
                {"role": "assistant", "content": self._answer_text()},
            ]})
        if path == "/api/agent/initiate":
            if not raw:
                return self._send(422, {"detail": [{"loc": ["body", "prompt"], "msg": "Field required",
                                                    "type": "missing", "input": None}]})
            tid = "th_" + uuid.uuid4().hex[:6]
            rid = _new_run(tid)
            if STATE.get("prepare_fail_once"):
                STATE["prepare_fail_once"] = False
                STATE["failed_runs"].add(rid)
            return self._send(200, {"thread_id": tid, "agent_run_id": rid})
        m = re.match(r"^/api/thread/([^/]+)/agent/start$", path)
        if m:
            tid = m.group(1)
            active = [r for r in STATE["threads"].get(tid, {}).get("runs", [])
                      if _status(STATE["runs"][r]) == "running"]
            if active:
                return self._send(409, {"detail": "This conversation already has an active task. "
                                                  "Send a follow-up or queue a question."})
            return self._send(200, {"agent_run_id": _new_run(tid)})
        return self._send(404, {"detail": "not found"})


def start(port=0):
    """起在后台线程，返回 (server, base_url)。port=0 表示随机端口。"""
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, "http://127.0.0.1:%d" % srv.server_address[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8899)
    args = ap.parse_args()
    srv, url = start(args.port)
    print("mock sciencepal →", url)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.shutdown()


if __name__ == "__main__":
    main()
