# -*- coding: utf-8 -*-
"""
sciencepal.py — 把平台当成"大脑"接入（API 适配层，带 422 自愈）。

已实测到的事实（来自你的 Key）：
    POST /api/agent/initiate  → 422 {"detail":[{"loc":["body","prompt"],"msg":"Field required"}]}
说明：**Key 有效、网络通**，只是请求体字段名不对（平台要 `prompt`）。

因此本层做三件事：
1) 先按平台的语义发（JSON，含 prompt / agent_id）；
2) 遇 422 时读 detail 里的缺失字段，按已知别名补齐后重试（最多两轮）；
3) 仍失败则把每一步的状态码与原文写进 workspaces/<sid>/sp_raw/，并给出人话提示。

已知端点（来自平台 OpenAPI；未登录 401，故仅能按公开结构适配）：
    GET  /api/agents
    POST /api/agent/initiate
    POST /api/thread/{thread_id}/agent/start
    GET  /api/agent-run/{id}  /  /stream
"""

from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid

from brains import Brain, BrainError

DEFAULT_BASE = "https://sciencepal.ai"
# 2026-10-30 实测可用的自定义 Agent「HCC-Fit」；内置后界面不再要求填 Base/Agent。
# 如需换 Agent：设环境变量 SCIENCEPAL_AGENT_ID（传空字符串则回退按名称查找/平台自动路由）。
DEFAULT_AGENT_ID = "61033129-3c47-4e72-885d-bcd4bb3e280a"
DEFAULT_AGENT_NAME = "HCC-Fit"

TEXT_FIELDS = {"prompt", "message", "query", "content", "input", "text", "question"}
ID_FIELDS = {"agent_id", "agentId", "agent"}
LIST_FIELDS = {"files", "attachments", "documents"}

_INVISIBLE = r"[\u200b-\u200f\u2028-\u202f\u2060\ufeff\u00a0\u3000\s]"


def _clean_key(raw):
    """清掉 Key 里的不可见字符 / 全角空格 / 换行，并检查是否混入非 ASCII。

    返回 (key, issue_msg)。issue_msg 非空时，任何请求都会直接报这句人话，
    而不是抛 `'latin-1' codec can't encode ...` 这种看不懂的错。
    """
    s = (raw or "").replace("\r", "").replace("\n", "").strip()
    s = re.sub(r"^bearer\s*", "", s, flags=re.I)      # 先去掉前缀，再清空格
    s = re.sub(_INVISIBLE, "", s)
    bad = [c for c in s if ord(c) > 127]
    if bad:
        msg = ("API Key 里含非 ASCII 字符（%s）。复制时多半带进了中文标点、全角空格或零宽字符。\n"
               "请到平台重新复制一次 Key，只保留 sk- 开头的 ASCII 串。"
               % "、".join(repr(c) for c in bad[:5]))
        return s, msg
    return s, None


class SciencePalBrain(Brain):
    name = "sciencepal"
    available = True

    def __init__(self, workspace, api_key=None, base=None, agent_name=None, timeout=180,
                 strict_agent=False, agent_id=None, **opts):
        super().__init__(workspace, **opts)
        raw_key = (api_key or os.environ.get("SCIENCEPAL_API_KEY") or "")
        self.key, self._key_issue = _clean_key(raw_key)
        self.base = (base or os.environ.get("SCIENCEPAL_BASE_URL") or DEFAULT_BASE).rstrip("/")
        self.agent_name = agent_name or DEFAULT_AGENT_NAME
        # 内置 Agent ID；显式传 "" 或设置 SCIENCEPAL_AGENT_ID="" 可关掉内置、改回按名称查找
        if agent_id is None:
            agent_id = os.environ.get("SCIENCEPAL_AGENT_ID", DEFAULT_AGENT_ID)
        self.agent_id = str(agent_id or "").strip()
        self.timeout = timeout
        self.strict_agent = strict_agent      # 单次核查模式：找不到指定 Agent 就报错，不静默自动路由
        self._thread_id = None
        self._direct_run = None
        self._agent_id_cache = None
        self.last_text = None
        self.degraded_files = False
        self._require_report = False
        self.raw_dir = os.path.join(workspace, "sp_raw")
        os.makedirs(self.raw_dir, exist_ok=True)

    def key_summary(self):
        """给界面看的 Key 摘要（不泄露内容）。"""
        if not self.key:
            return "空"
        return "长度 %d，形如 %s…%s" % (len(self.key), self.key[:4], self.key[-4:])

    # ---------------- 低层 ----------------
    def _req(self, method, path, data=None, headers=None, timeout=None):
        if self._key_issue:
            raise BrainError(self._key_issue)
        url = self.base + path
        hdrs = {"Accept": "application/json"}
        if self.key:
            hdrs["Authorization"] = "Bearer " + self.key
        if headers:
            hdrs.update(headers)
        for name, value in hdrs.items():          # 提前拦掉非 ASCII，避免 latin-1 报错
            try:
                str(value).encode("latin-1")
            except UnicodeEncodeError:
                bad = [c for c in str(value) if ord(c) > 127]
                raise BrainError("请求头 %s 含非 ASCII 字符（%s）。"
                                 "通常是复制 Key 时带进了中文标点/全角空格/零宽字符，请重新复制。"
                                 % (name, "、".join(repr(c) for c in bad[:5])))
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        try:
            with urllib.request.urlopen(req, timeout=timeout or self.timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except Exception as exc:  # 网络/证书/超时
            raise BrainError("连不上 %s：%s（检查网络、代理、证书）" % (url, exc))

    def _json(self, method, path, payload=None, timeout=None):
        data = None
        hdrs = {}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            hdrs["Content-Type"] = "application/json; charset=utf-8"
        status, raw = self._req(method, path, data=data, headers=hdrs, timeout=timeout)
        try:
            body = json.loads(raw) if raw.strip() else {}
        except json.JSONDecodeError:
            body = {"_raw": raw[:1500]}
        return status, body

    def _log(self, msg):
        """人可读的运行日志：workspaces/<sid>/run.log（每阶段都写，出问题直接看这个）"""
        try:
            with open(os.path.join(self.workspace, "run.log"), "a", encoding="utf-8") as fh:
                fh.write("%s %s\n" % (time.strftime("%H:%M:%S"), msg))
        except Exception:
            pass

    def _dump(self, name, status, body):
        try:
            stamp = time.strftime("%H%M%S") + "-" + uuid.uuid4().hex[:4]
            path = os.path.join(self.raw_dir, "%s_%s.json" % (name, stamp))
            with open(path, "w", encoding="utf-8") as fh:
                json.dump({"status": status, "body": body}, fh, ensure_ascii=False, indent=2)
        except Exception:
            pass

    @staticmethod
    def _walk(obj, needles):
        """在返回结构里递归找形如 <needle>_id 的字符串值。"""
        if isinstance(obj, dict):
            for k, v in obj.items():
                lk = k.lower()
                if isinstance(v, str) and any(n in lk for n in needles) and ("id" in lk or lk in needles):
                    return v
            for v in obj.values():
                got = SciencePalBrain._walk(v, needles)
                if got:
                    return got
        elif isinstance(obj, list):
            for v in obj:
                got = SciencePalBrain._walk(v, needles)
                if got:
                    return got
        return None

    @staticmethod
    def _missing_fields(body):
        out = []
        detail = body.get("detail") if isinstance(body, dict) else None
        if isinstance(detail, list):
            for item in detail:
                if isinstance(item, dict):
                    loc = item.get("loc") or []
                    if loc:
                        out.append(str(loc[-1]))
                elif isinstance(item, str):
                    out.append(item)
        elif isinstance(detail, str):
            out.append(detail)
        return out

    @staticmethod
    def _http_hint(what, status, body):
        """把平台 HTTP 码翻译成可操作的提示（官方错误语义）。"""
        hint = {
            401: "API Key 无效、过期或未授权",
            403: "没有该 Agent / 模型的权限，或密钥无权访问",
            404: "找不到 Agent 或接口（确认 Agent 名称与 Base 地址）",
            422: "参数不被接受（常见：模型在该账号不可用；本层未指定模型时请联系平台）",
            429: "额度用尽或被限流，请检查账户额度后重试",
        }.get(status, "HTTP %s" % status)
        return "%s失败：%s；返回：%s" % (
            what, hint, json.dumps(body, ensure_ascii=False)[:300])

    # ---------------- 探测 ----------------
    def probe(self):
        if self._key_issue:
            return False, self._key_issue, []
        if not self.key:
            return False, "没有填 API Key", []
        status, body = self._json("GET", "/api/agents")
        if status == 401:
            return False, "401：Key 无效或未被授权（%s）" % self.key_summary(), []
        if status != 200:
            return False, "%s（Key %s）" % (self._http_hint("探测 Agent 列表", status, body),
                                            self.key_summary()), []
        items = body.get("agents") or body.get("items") or body.get("data") or []
        names = [a.get("name") for a in items if isinstance(a, dict)]
        real = [n for n in names if n]
        if self.agent_id:
            ids = [str(a.get("id") or a.get("agent_id") or "") for a in items if isinstance(a, dict)]
            if any(i == self.agent_id for i in ids):
                return True, "能连上", []     # 已连到内置 Agent：不再追加名字/Key 尾巴
            if real:
                return False, ("能连上，但当前 Key 看不到内置 Agent（可能需要授权或换 SCIENCEPAL_AGENT_ID）；"
                               "可见：%s" % "、".join(real[:8])), real
            return True, "能连上", []
        hit = any((n or "").strip().lower() == self.agent_name.strip().lower() for n in names)
        if not real or hit:
            return True, "能连上", []
        return False, ("能连上，但未匹配到「%s」；可见：%s"
                       % (self.agent_name, "、".join(real[:8]))), real

    def _agent_names(self):
        status, body = self._json("GET", "/api/agents")
        items = body.get("agents") or body.get("items") or body.get("data") or []
        return [a.get("name") for a in items if isinstance(a, dict) and a.get("name")]

    def _agent_id(self):
        if self.agent_id:
            return self.agent_id          # 内置/显式指定：不再查 /api/agents
        if self._agent_id_cache is not None:
            return self._agent_id_cache
        status, body = self._json("GET", "/api/agents")
        items = body.get("agents") or body.get("items") or body.get("data") or []
        for a in items:
            if isinstance(a, dict) and (a.get("name") or "").strip().lower() == self.agent_name.strip().lower():
                self._agent_id_cache = a.get("id") or a.get("agent_id")
                return self._agent_id_cache
        self._agent_id_cache = None
        return None

    # ---------------- 会话 ----------------
    @staticmethod
    def _multipart(fields, files):
        boundary = "----hccfit" + uuid.uuid4().hex
        chunks = []
        for k, v in (fields or {}).items():
            if isinstance(v, bool):
                v = "true" if v else "false"
            elif isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            chunks.append(("--%s\r\nContent-Disposition: form-data; name=\"%s\"\r\n\r\n%s\r\n"
                           % (boundary, k, v)).encode("utf-8"))
        for path in files or []:
            if not os.path.isfile(path):
                continue
            with open(path, "rb") as fh:
                blob = fh.read()
            chunks.append(("--%s\r\nContent-Disposition: form-data; name=\"files\"; filename=\"%s\"\r\n"
                           "Content-Type: application/octet-stream\r\n\r\n"
                           % (boundary, os.path.basename(path))).encode("utf-8"))
            chunks.append(blob)
            chunks.append(b"\r\n")
        chunks.append(("--%s--\r\n" % boundary).encode("utf-8"))
        return b"".join(chunks), "multipart/form-data; boundary=%s" % boundary

    def _heal(self, payload, missing, prompt):
        """按 422 的缺失字段补齐；无法判断的字段返回 None。"""
        unknown = []
        for field in missing:
            if field in payload:
                continue
            if field in TEXT_FIELDS:
                payload[field] = prompt
            elif field in ID_FIELDS:
                aid = self._agent_id()
                if not aid:
                    continue        # 拿不到就交给平台自动路由，不拦
                payload[field] = aid
            elif field in LIST_FIELDS:
                payload[field] = []
            else:
                unknown.append(field)
        if unknown:
            raise BrainError("平台要求这些字段，但本层不知道该填什么：%s\n"
                             "把这条报错发我，我加进映射。" % "、".join(unknown))
        return payload

    def _initiate(self, prompt, files):
        """依次尝试：form(带文件) → form(补齐字段) → json(补齐字段)。

        依据实测：平台返回 422 且 `input: None`，说明它按 Form 解析请求体，
        我们发 JSON 时它"看不到" prompt —— 所以 **form 优先**。
        """
        if self._thread_id:
            return {"thread_id": self._thread_id, "run_id": None}

        payload = {"prompt": prompt}
        aid = self._agent_id()
        if aid:                              # 找到了就显式指定；找不到就让平台自动路由（不拦）
            payload["agent_id"] = aid
            payload["agent_selection_explicit"] = True
            payload["agent_select_type"] = "manual"
        else:
            if self.strict_agent:
                names = self._agent_names()
                if names:
                    raise BrainError("找不到 Agent「%s」。当前 Key 可见：%s"
                                     % (self.agent_name, "、".join(names[:8])))
                self._log("strict_agent: /api/agents 没列出 Agent（可能 Key 权限受限），改为自动路由")
            self._dump("route_auto", 0, {"note": "/api/agents 为空或未匹配，交给平台自动路由",
                                         "want": self.agent_name})

        def send(kind, body):
            if kind == "form":
                data, ctype = self._multipart(body, files)
                s, raw = self._req("POST", "/api/agent/initiate", data=data,
                                   headers={"Content-Type": ctype})
                try:
                    return s, json.loads(raw)
                except json.JSONDecodeError:
                    return s, {"_raw": raw[:1500]}
            return self._json("POST", "/api/agent/initiate", body)

        last_resp = None
        for kind in ("form", "form", "json"):
            status, resp = send(kind, payload)
            last_resp = resp
            self._dump("initiate_%s" % kind, status, resp)
            self._log("initiate[%s] -> %s %s" % (kind, status, str(resp)[:160]))

            if status == 200:
                # initiate 通常"建会话 + 立刻起任务"，所以这里要同时捞 thread 和 run
                tid = self._walk(resp, ["thread"]) or self._walk(resp, ["conversation"])
                rid = self._walk(resp, ["agent_run", "run", "job", "task"])
                if tid:
                    self._thread_id = tid
                    if rid:
                        self._direct_run = rid
                    return {"thread_id": tid, "run_id": rid}
                if rid:
                    self._direct_run = rid
                    return {"thread_id": None, "run_id": rid}
                raise BrainError("initiate 返回 200，但既没有 thread_id 也没有 run_id：%s"
                                 % json.dumps(resp, ensure_ascii=False)[:300])
            if status == 401:
                raise BrainError("401：Key 无效或未被授权")
            if status in (403, 404) and payload.get("agent_id"):
                # 这个 Key 用不了内置/指定的 Agent：退回按名称查找或平台自动路由
                self._log("agent %s 不可用（HTTP %s），改为按名称/自动路由重试"
                          % (payload.get("agent_id"), status))
                self.agent_id = ""
                self._agent_id_cache = None
                for k in ("agent_id", "agent_select_type", "agent_selection_explicit"):
                    payload.pop(k, None)
                continue
            if status == 422:
                missing = self._missing_fields(resp)
                need = [m for m in missing if m not in payload]
                if not need:
                    continue          # 字段都在，换编码再试
                payload = self._heal(payload, need, prompt)
                continue
            raise BrainError(self._http_hint("initiate", status, resp))

        raise BrainError("initiate 失败：form/json 各试过仍被拒。最后一次返回：%s\n"
                         "请把 workspaces/<sid>/sp_raw/initiate_*.json 发我。"
                         % json.dumps(last_resp, ensure_ascii=False)[:400])

    # ---- 会话内的任务调度 ----
    def _active_run(self, tid):
        """会话里是否已有活跃任务（initiate 会直接起一个，这是 409 的来源）。"""
        status, body = self._json("GET", "/api/thread/%s/agent-runs" % tid)
        if status != 200:
            return None
        runs = body.get("agent_runs") or body.get("runs") or body.get("items") or []
        for r in runs if isinstance(runs, list) else []:
            if isinstance(r, dict) and str(r.get("status", "")).lower() in (
                    "running", "pending", "queued", "in_progress", "started"):
                return r.get("id") or r.get("agent_run_id")
        return None

    def _wait_idle(self, tid, limit=90):
        end = time.time() + limit
        while time.time() < end:
            if not self._active_run(tid):
                return True
            time.sleep(1.5)
        return False

    def _start_followup(self, tid, prompt):
        """平台的追问 = **先写一条用户消息，再用 message_id 起跑**。
        （AgentStartRequest 里没有 prompt 字段；直接传 prompt 会被忽略 → 空跑，tokens_used=0）"""
        mid = str(uuid.uuid4())
        status, body = self._json("POST", "/api/thread/%s/messages" % tid,
                                  {"message_id": mid, "content": prompt})
        self._dump("add_message", status, body)
        self._log("add_message -> %s %s" % (status, str(body)[:120]))
        if status != 200:
            raise BrainError(self._http_hint("写入用户消息", status, body))
        mid = (body or {}).get("message_id") or mid

        def start():
            req = {"message_id": mid, "agent_selection_explicit": True, "stream": True}
            aid = self._agent_id()
            if aid:
                req["agent_id"] = aid
            return self._json("POST", "/api/thread/%s/agent/start" % tid, req)

        status, body = start()
        self._dump("start", status, body)
        self._log("agent/start -> %s %s" % (status, str(body)[:120]))
        if status == 409:                      # 上一轮还没收尾：等它空下来再起
            self._wait_idle(tid)
            status, body = start()
            self._dump("start_retry", status, body)
        if status != 200:
            raise BrainError(self._http_hint("agent/start", status, body))
        rid = self._walk(body, ["agent_run", "run"]) or body.get("id")
        if not rid:
            raise BrainError("agent/start 没返回 run_id：%s" % json.dumps(body, ensure_ascii=False)[:300])
        return rid

    def _poll(self, rid, tid=None, stage=None):
        deadline = time.time() + self.timeout
        last = {}
        while time.time() < deadline:
            status, info = self._json("GET", "/api/agent-run/%s" % rid)
            last = info
            state = str(info.get("status") or info.get("state") or info.get("executionState") or "").lower()
            if state in ("completed", "finished"):
                break
            if state == "asked":
                # 官方语义：asked = 等用户回答，不是完成；不能当“还在跑”空等到超时
                raise BrainError(
                    "Agent 在追问（asked），需要先回答才能继续；当前版本不会自动回答。"
                    "可到 SciencePal 网页继续该会话，或换一个不追问的 Agent/提示词。"
                    "（thread_id=%s, run_id=%s）" % (tid or self._thread_id or "-", rid))
            if state in ("failed", "error", "stopped"):
                err = info.get("error") or info.get("detail") or ""
                raise BrainError("Agent 运行失败（%s）：%s%s"
                                 % (state, str(err)[:300],
                                    "" if err else json.dumps(info, ensure_ascii=False)[:400]))
            time.sleep(1.5)
        self._dump("run_%s" % rid, 200, last)
        self._log("run %s -> %s" % (rid, str(last)[:160]))

        # 顺序：run 自带的 responses → 流式重放（after_index=0）→ run 全文
        text = ""
        for key in ("responses", "messages", "output", "content", "result", "answer"):
            val = last.get(key) if isinstance(last, dict) else None
            if not val:
                continue
            text = self._pick_final_text(val) if isinstance(val, list) else self._all_text(val)
            if text.strip():
                break
        if not text.strip():
            text = self._fetch_stream(rid)
        if not text.strip():
            text = self._all_text(last)
        if not text.strip():
            raise BrainError("运行已完成，但流里没有任何正文（tokens_used 可能是 0）。"
                             "原始事件已存到 %s，请把 stream_*.json 发我。" % self.raw_dir)
        return self._parse_answer(text, stage)

    # ---- 控制面噪音过滤：真平台的 messages 里混着 tool_started / function_calls / 路由信息 ----
    NOISE_MARKERS = ("<function_calls>", "tool_started", "tool_finished", "tool_index",
                     "auto_judge_agent", "routing_decision", "xml_tag_name", "<invoke",
                     "session_name", "exit_code", "output_original_chars", "tool_call_id")

    @staticmethod
    def _coerce_text(raw):
        if isinstance(raw, str):
            return raw
        if isinstance(raw, list):
            parts = []
            for x in raw:
                if isinstance(x, dict):
                    parts.append(str(x.get("text") or x.get("content") or ""))
                else:
                    parts.append(str(x))
            return "\n".join(p for p in parts if p.strip())
        if isinstance(raw, dict):
            for k in ("text", "content", "message", "answer", "output"):
                if isinstance(raw.get(k), str):
                    return raw[k]
            # 有些实现直接把报告对象放在 content 里
            if any(k in raw for k in ("population_check", "overall_verdict", "matched", "summary_zh")):
                return json.dumps(raw, ensure_ascii=False)
            return ""
        return ""

    def _pick_final_text(self, items):
        """从消息列表里挑出"最终的自然语言回答"，跳过工具调用、工具输出与过程旁白。"""
        cands = []
        for m in items if isinstance(items, list) else []:
            if not isinstance(m, dict):
                continue
            role = str(m.get("role") or m.get("author") or "").lower()
            if role and role not in ("assistant", "agent", "ai", "bot"):
                continue
            st = str(m.get("status_type") or m.get("type") or "").lower()
            if st in ("tool_started", "tool_finished", "thinking", "routing", "tool_call"):
                continue
            text = self._coerce_text(m.get("content") if "content" in m else m.get("text") or m)
            text = (text or "").strip()
            if not text:
                continue
            low = text.lower()
            if any(mk in low for mk in self.NOISE_MARKERS):
                continue
            # 工具执行结果（exec 的 {"output":..., "session_name":...}）不是最终回答
            if low.startswith("{") and any(k in low for k in ('"output"', "session_name", "exit_code")):
                continue
            # 过程旁白（很短且以 "I'll / Let me / PDF extracted" 开头）
            if len(text) < 400 and re.match(r"^(i'?ll |i will |let me |pdf extracted|reading the pdf|i'?m reading|now i )", low):
                continue
            cands.append(text)
        report_cands = [c for c in cands
                        if any(k in c for k in ('"population_check"', '"overall_verdict"',
                                                '"matched"', '"summary_zh"', '"overall_summary"'))]
        if report_cands:
            return report_cands[-1]
        return cands[-1] if cands else ""

    def _fetch_messages(self, tid):
        """平台**没有**"读消息"接口：POST /thread/{id}/messages 是"写用户消息"。
        这里只保留一次 GET 探测（拿 405 也无所谓），绝不写会话。"""
        if not tid:
            return ""
        status, body = self._json("GET", "/api/thread/%s/messages" % tid)
        self._dump("messages_get", status, body)
        return ""

    @staticmethod
    def _stream_text(obj):
        """真平台的事件是**两层 JSON**：
           外层 {"type":"assistant","is_llm_message":true,
                 "content":"{\\"role\\":\\"assistant\\",\\"content\\":\\"<增量片段>\\"}"}
        所以要把内层 content 解出来当增量；status 事件一律跳过。"""
        noise = SciencePalBrain.NOISE_MARKERS
        if isinstance(obj, str):
            return "" if any(mk in obj.lower() for mk in noise) else obj
        if not isinstance(obj, dict):
            return ""

        etype = str(obj.get("type") or "").lower()
        stype = str(obj.get("status_type") or "").lower()
        if etype in ("status",) or stype in ("tool_started", "tool_finished", "thinking", "routing",
                                             "model_resolved", "finishing", "run_progress_end",
                                             "thread_run_start", "assistant_response_start", "started"):
            return ""

        raw = obj.get("content")
        if isinstance(raw, str) and raw.strip().startswith("{"):
            try:                                    # 内层：{"role":..., "content":"<片段>"}
                inner = json.loads(raw)
                if isinstance(inner, dict) and isinstance(inner.get("content"), str):
                    piece = inner["content"]
                    return "" if any(mk in piece.lower() for mk in noise) else piece
            except json.JSONDecodeError:
                pass
        if isinstance(raw, str) and raw.strip() and not isinstance(obj.get("choices"), list):
            if obj.get("is_llm_message") or etype in ("assistant", "message", "delta"):
                return "" if any(mk in raw.lower() for mk in noise) else raw

        choices = obj.get("choices")
        if isinstance(choices, list) and choices:
            d = choices[0].get("delta") or choices[0].get("message") or {}
            if isinstance(d, dict) and isinstance(d.get("content"), str):
                return d["content"]
        for k in ("delta", "text", "message", "answer", "output"):
            v = obj.get(k)
            if isinstance(v, str) and v.strip():
                return "" if any(mk in v.lower() for mk in noise) else v
        return ""

    TERMINAL = ("run_progress_end", "done", "end", "completed", "finished", "error")

    @staticmethod
    def _is_tool_event(obj):
        """工具调用/工具结果事件：遇到它就把"当前段"截断，后面才是最终回答。"""
        if not isinstance(obj, dict):
            return False
        st = str(obj.get("status_type") or obj.get("type") or "").lower()
        if st in ("tool_started", "tool_finished", "tool_call", "function_call", "tool_result"):
            return True

        def is_tool_payload(d):
            return isinstance(d, dict) and any(
                k in d for k in ("session_name", "exit_code", "output_original_chars",
                                 "tool_call_id", "stdout"))

        raw = obj.get("content")
        if isinstance(raw, str) and raw.strip().startswith("{"):
            try:
                inner = json.loads(raw)
            except json.JSONDecodeError:
                return False
            if is_tool_payload(inner):
                return True
            if isinstance(inner.get("content"), str) and inner["content"].strip().startswith("{"):
                try:
                    if is_tool_payload(json.loads(inner["content"])):
                        return True
                except json.JSONDecodeError:
                    pass
        return False

    @staticmethod
    def _looks_like_final(text):
        """判断当前段是否已经像"最终回答"，避免在工具阶段结束时提前断开。"""
        t = (text or "").strip()
        if not t:
            return False
        if any(k in t for k in ('"population_check"', '"overall_verdict"', '"matched"',
                                '"summary_zh"', '"overall_summary"')):
            return True
        if len(t) > 800 and not re.match(r"^(i'?ll |let me |pdf extracted|reading the pdf)", t.lower()):
            return True
        return False

    def _replay_stream(self, rid, after=0):
        """**关键**：流的默认行为是从"当前尾部"订阅；答案早就过去了。
        必须带 after_index=0 让 Redis List 从头重放，才能拿到完整应答。

        工具型 Agent 会先 exec 读 PDF，再输出报告；工具事件之后的文本才是最终回答。
        所以这里按工具事件分段，最后取"最后一段非空文本"。"""
        url = "%s/api/agent-run/%s/stream?after_index=%d" % (self.base, rid, after)
        hdrs = {"Accept": "text/event-stream"}
        if self.key:
            hdrs["Authorization"] = "Bearer " + self.key
        lines, segments = [], [[]]
        try:
            req = urllib.request.Request(url, headers=hdrs, method="GET")
            with urllib.request.urlopen(req, timeout=min(120, self.timeout)) as resp:
                for raw in resp:
                    line = raw.decode("utf-8", "replace").strip()
                    if not line.startswith("data:"):
                        continue
                    payload = line[5:].strip()
                    if payload in ("", "[DONE]"):
                        continue
                    lines.append(payload[:4000])
                    try:
                        obj = json.loads(payload)
                    except json.JSONDecodeError:
                        segments[-1].append(payload)
                        continue
                    if self._is_tool_event(obj):
                        segments.append([])
                        continue
                    piece = self._stream_text(obj)
                    if piece:
                        segments[-1].append(piece)
                    st = str(obj.get("type") or obj.get("status_type") or "").lower()
                    if st == "error":
                        break
                    # 收尾事件：只有当前段已经像最终回答才停；
                    # 否则可能只是某个工具阶段结束，继续读完整个流
                    if st in self.TERMINAL and self._looks_like_final("".join(segments[-1])):
                        break
        except Exception as exc:  # 流可能已结束/超时
            self._dump("stream_err", 0, {"error": str(exc), "after": after})
        text = ""
        for seg in reversed(segments):
            candidate = "".join(seg).strip()
            if candidate:
                text = candidate
                break
        if not text:
            text = "".join("".join(seg) for seg in segments).strip()
        self._dump("stream_%s_after%d" % (rid, after), 200, {"lines": lines[-400:]})
        self._log("stream %s after=%d: events=%d chars=%d" % (rid, after, len(lines), len(text)))
        return text

    def _fetch_stream(self, rid):
        text = self._replay_stream(rid, 0)
        if not text:
            text = self._replay_stream(rid, -1)     # 有些实现认 -1 = 从头
        return text

    def _parse_answer(self, text, stage=None):
        """JSON → 最小必需归一；失败就退回纯文本（上层降级，不中断）。"""
        require_report = bool(getattr(self, "_require_report", False))
        try:
            obj = self._extract_json({"text": text}, require_report=require_report)
        except BrainError:
            self.last_text = text[:8000]
            self._log("parse: 没有可用的 JSON，降级为文本 %r" % text[:120])
            return {"_parse": "text", "_text": text[:8000]}
        self.last_text = text[:8000]
        self._log("parse: JSON ok, keys=%s" % (sorted(obj.keys())[:8] if isinstance(obj, dict) else type(obj).__name__))
        from contract import normalize_stage
        out = normalize_stage(stage, obj) if stage else obj
        if isinstance(out, dict) and stage:
            out["_raw_keys"] = sorted(obj.keys())[:12] if isinstance(obj, dict) else []
        return out

    PREPARE_FAIL = ("workspace_context_prepare_failed", "prepare the selected files")

    def _run_agent(self, prompt, files, stage=None):
        try:
            return self._run_once(prompt, files, stage)
        except BrainError as exc:
            msg = str(exc)
            if not any(k in msg for k in self.PREPARE_FAIL):
                raise
            return self._retry_without_files(prompt, files, stage, msg)

    def _retry_without_files(self, prompt, files, stage, why):
        """平台没能"准备"附件时：把 PDF 文字抠出来塞进 prompt，不带文件重跑。"""
        import pdftext
        text, note = "", ""
        for f in files or []:
            if str(f).lower().endswith(".pdf"):
                text, note = pdftext.extract(f)
                if text:
                    break
        self.degraded_files = True
        self._dump("prepare_retry", 0, {"why": why[:300], "note": note, "chars": len(text)})
        extra = "\n\n【附件未被平台解析】%s\n" % (note or "原因未知")
        if text:
            extra += ("以下是该 PDF 能被抽出的文字，请据此作答；拿不准就写 unknown。\n"
                      + text[:9000])
        else:
            extra += "本次拿不到论文正文，请只基于场景作答，并在结论里明确标注依据不足。\n"
        self._thread_id = None if not self._thread_id else self._thread_id
        return self._run_once(prompt + extra, [], stage)

    def _run_once(self, prompt, files, stage=None):
        if self._thread_id:                    # 后续阶段：在同一会话里"追问"
            rid = self._start_followup(self._thread_id, prompt)
        else:                                  # 第一阶段：initiate 建会话（通常已起任务）
            info = self._initiate(prompt, files)
            tid, rid = info.get("thread_id"), info.get("run_id")
            if not rid and tid:
                rid = self._active_run(tid)    # 会话里已有任务：直接轮询它
            if not rid and tid:
                rid = self._start_followup(tid, prompt)
            if not rid:
                raise BrainError("既没有拿到 run_id，也没法在会话里起任务")
        return self._poll(rid, self._thread_id, stage)

    @staticmethod
    def _all_text(obj):
        if isinstance(obj, str):
            return obj
        if isinstance(obj, dict):
            return "\n".join(SciencePalBrain._all_text(v) for v in obj.values())
        if isinstance(obj, list):
            return "\n".join(SciencePalBrain._all_text(v) for v in obj)
        return ""

    REPORT_KEY_HINTS = ("population_check", "timepoint_check", "input_check", "outcome_check",
                        "overall_verdict", "overall_summary", "validation_evidence",
                        "matched", "summary_zh", "papers", "expected_data", "status")

    @staticmethod
    def _iter_json(text):
        """从文本里逐个抠出可解析的 JSON 对象（支持多个块、嵌套、工具输出前后混杂）。"""
        decoder = json.JSONDecoder()
        i = 0
        while True:
            i = text.find("{", i)
            if i < 0:
                return
            try:
                obj, end = decoder.raw_decode(text[i:])
            except Exception:
                i += 1
                continue
            yield obj
            i += end

    @staticmethod
    def _find_report(obj):
        """在任意嵌套里找"核查报告"对象（有些实现会包一层 {"report": {...}}）。"""
        keys = ("population_check", "timepoint_check", "input_check", "outcome_check",
                "overall_verdict", "matched", "summary_zh")
        if isinstance(obj, dict):
            if any(k in obj for k in keys):
                return obj
            for v in obj.values():
                got = SciencePalBrain._find_report(v)
                if got:
                    return got
        elif isinstance(obj, list):
            for v in obj:
                got = SciencePalBrain._find_report(v)
                if got:
                    return got
        return None

    def _extract_json(self, payload, require_report=False):
        text = self._all_text(payload)
        objs = [o for o in self._iter_json(text) if isinstance(o, dict)]
        if require_report:
            for o in reversed(objs):
                report = self._find_report(o)
                if report:
                    return report
            raise BrainError("Agent 返回里没有找到核查报告 JSON（只有工具输出或纯文本）；原文已存到 %s" % self.raw_dir)
        if objs:
            # 优先挑像"阶段答案"的对象，避免把 exec 的 {"output":...} 当成答案
            scored = [(sum(1 for k in self.REPORT_KEY_HINTS if k in o), idx, o)
                      for idx, o in enumerate(objs)]
            scored.sort(key=lambda x: (x[0], x[1]))
            return scored[-1][2]
        raise BrainError("Agent 返回里找不到可解析的 JSON；原文已存到 %s" % self.raw_dir)

    # ---------------- 流水线接口 ----------------
    # 每个阶段：一句任务 + **最小必需结构**（只要求前端渲染非空，不要求把它说完）
    # 刻意放宽：中文键名、字段可缺、可加额外字段、可以用工具——只要求"最后一条消息里有 json 块"。
    STAGE_PROMPTS = {
        "domain": (
            '判断这个场景是否属于"肝癌（肝脏原发肿瘤）的治疗或诊断相关"。',
            '{"status":"in_scope | outside_scope | unclear","note_zh":"理由（可长可短）"}'),
        "papers": (
            '从上传文件与平台检索结果里，挑出与场景相关的预测模型论文。',
            '{"papers":[{"title":"论文标题","decision":"include | exclude","reason_zh":"理由"}]}'),
        "match": (
            '对每篇纳入论文做四维匹配：population / timepoint / input / outcome。',
            '{"matched":[{"paper_id":"p1","tier":"applicable | conditional | undetermined | not_applicable",'
            '"dimensions":{"population":"match|mismatch|unknown","timepoint":"...","input":"...","outcome":"..."}}]}'),
        "integrate": (
            '综合所有匹配结果，给出可用于沟通的方案与注意点（不要给治疗建议）。',
            '{"summary_zh":"一段综合结论","options":[{"name":"方案名","note_zh":"说明"}],"cautions_zh":["注意点"]}'),
        "expected": (
            '列出为提升可靠性还应当补充的数据/资料（化验、影像、病理、病史都算）。',
            '{"expected_data":[{"item_zh":"要补的东西","why_zh":"为什么"}]}'),
    }

    CONTRACT = (
        "【返回规范｜放宽版】\n"
        "· 你可以使用平台工具（读 PDF、检索、沙盒、子代理）——想怎么用都行，不影响交付。\n"
        "· 只要你的**最后一条消息**里有下面这种 ```json 代码块即可；前面想写多少中文结论都行。\n"
        "· **只要求下面列出的字段**，其它字段随你加；字段名用英文或中文都可以"
        "（例如 status 也可以写成「结论」，note_zh 写成「理由」）。\n"
        "· 拿不准的字段可以省略，或写 unknown / 未给出；**不要为了凑字段编造**。\n"
        "· 不要输出 XML / function_calls 作为最终内容。\n\n"
    )

    def run_stage(self, stage: str, payload: dict) -> dict:
        if not self.key:
            raise BrainError("没有 API Key：请在“3 · 大脑”里填入 SciencePal API Key")
        from hcc_report import attachments_block

        task, skeleton = self.STAGE_PROMPTS[stage]
        atts = [a for a in (payload.get("attachments") or []) if str(a).strip()]
        prompt = (
            self.CONTRACT
            + "【本阶段任务】%s\n\n"
            "【最小必需结构（照这个形状给，字段可增减）】\n```json\n%s\n```\n\n"
            "【场景】\n%s\n\n【可用文件（临时位点，可直接读取）】\n%s\n\n"
            "【论文正文片段（用户粘贴，可作为主要依据）】\n%s\n\n%s【上一阶段结果】\n%s\n"
            % (task, skeleton,
               json.dumps({k: v for k, v in payload.get("scenario", {}).items()
                           if k != "paper_text"}, ensure_ascii=False, indent=2),
               "\n".join(payload.get("files", [])) or "（无）",
               (payload.get("scenario", {}).get("paper_text") or "（无）")[:6000],
               attachments_block(atts),
               json.dumps({k: v for k, v in payload.items()
                           if k not in ("files", "attachments")}, ensure_ascii=False)[:4000])
        )
        files = [os.path.join(self.workspace, "files", f) for f in payload.get("files", [])]
        files += [os.path.join(self.workspace, "files", a) for a in atts]
        return self._run_agent(prompt, files, stage)

    def run_hcc_report(self, prompt, files=None):
        """单次核查：一条 prompt、一次 initiate、一次解析，不拆 S0–S4。

        要求最终 JSON 必须像"核查报告"（population_check/overall_verdict/matched 等），
        避免把 Agent 用 exec 读 PDF 的工具输出误当成报告。
        """
        if not self.key:
            raise BrainError("没有 API Key：请在界面填入 SciencePal Key")
        self._require_report = True
        try:
            return self._run_agent(prompt, files or [], None)
        finally:
            self._require_report = False

    def repair_hcc_json(self, previous_text=""):
        """二次修复：Agent 第一次回了 Markdown/分析，就让它把同一结论只输出成 JSON。"""
        from hcc_report import SCHEMA_HINT

        prompt = (
            "你上一条回答没有按 JSON 输出。现在不要重复分析过程，也不要解释，"
            "只把你已经得出的核查结论输出成一个 JSON 对象。\n\n"
            "【输出结构】\n%s\n\n"
            "【最终输出要求】只输出一个 JSON 对象；第一个字符必须是 {，最后一个字符必须是 }；"
            "不要 Markdown 代码块、不要前后说明。" % SCHEMA_HINT
        )
        if previous_text:
            prompt += "\n\n【你上一条回答（供参考，不要照抄格式）】\n" + previous_text[:6000]
        self._require_report = True
        try:
            return self._run_agent(prompt, [], None)
        finally:
            self._require_report = False
