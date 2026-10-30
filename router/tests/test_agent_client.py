# -*- coding: utf-8 -*-
"""SciencePal Agent 客户端测试（全部离线，mock requests）。

覆盖官方调用规则：
- initiate 的参数（agent_id/prompt/stream/agent_select_type；模型留空不传）
- initiate → stream（after_index=0）→ 终态确认
- asked = 等用户回答，必须报错而不是空等
- HTTP 429/401 的可操作提示
- 流为空时回退到 run 结构
"""
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hccfit import config  # noqa: E402
from hccfit import sciencepal  # noqa: E402


class FakeResp:
    def __init__(self, status=200, json_data=None, text="", lines=None):
        self.status_code = status
        self._json = json_data
        self.text = text
        self._lines = lines or []

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json

    def iter_lines(self, decode_unicode=False):
        for item in self._lines:
            yield item.encode("utf-8") if isinstance(item, str) else item

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def sse(*events):
    """把 [(type, content), ...] 变成 SSE 行。"""
    import json
    out = []
    for obj in events:
        out.append("data: " + json.dumps(obj, ensure_ascii=False))
    out.append("data: [DONE]")
    return out


class AgentClientTests(unittest.TestCase):
    def setUp(self):
        self._env = (config.SCIENCEPAL_API_KEY, config.SCIENCEPAL_MODEL,
                     config.SCIENCEPAL_BASE_URL, config.SCIENCEPAL_FILTER_AGENT_ID)
        config.SCIENCEPAL_API_KEY = "sp-test"
        config.SCIENCEPAL_MODEL = ""
        config.SCIENCEPAL_BASE_URL = "https://sciencepal.ai"
        config.SCIENCEPAL_FILTER_AGENT_ID = "agent-1"

    def tearDown(self):
        (config.SCIENCEPAL_API_KEY, config.SCIENCEPAL_MODEL,
         config.SCIENCEPAL_BASE_URL, config.SCIENCEPAL_FILTER_AGENT_ID) = self._env

    def test_happy_path(self):
        posts = []
        gets = []

        def fake_post(url, **kw):
            posts.append((url, kw))
            return FakeResp(200, {"thread_id": "t1", "agent_run_id": "r1"})

        stream = FakeResp(200, lines=sse(
            {"type": "status", "content": "started"},
            {"type": "assistant",
             "content": '{"role":"assistant","content":"{\\"keep\\":[]}"}'},
        ))

        def fake_get(url, **kw):
            gets.append((url, kw))
            if url.endswith("/stream"):
                return stream
            return FakeResp(200, {"status": "completed"})

        with mock.patch.object(sciencepal.requests, "post", fake_post), \
                mock.patch.object(sciencepal.requests, "get", fake_get):
            out = sciencepal.run_agent("agent-1", "hello", timeout_s=20)

        self.assertEqual(out["answer"], '{"keep":[]}')
        self.assertEqual(out["status"], "completed")
        self.assertEqual(out["run_id"], "r1")
        self.assertEqual(out["thread_id"], "t1")

        url, kw = posts[0]
        self.assertEqual(url, "https://sciencepal.ai/api/agent/initiate")
        self.assertEqual(kw["data"]["agent_id"], "agent-1")
        self.assertEqual(kw["data"]["prompt"], "hello")
        self.assertEqual(kw["data"]["stream"], "true")
        self.assertEqual(kw["data"]["agent_select_type"], "manual")
        self.assertNotIn("model_name", kw["data"])          # 留空=平台默认

        self.assertTrue(any("/agent-run/r1/stream" in u for u, _ in gets))
        self.assertTrue(any(kw.get("params") == {"after_index": 0}
                            for u, kw in gets if u.endswith("/stream")))

    def test_model_sent_only_when_configured(self):
        posts = []

        def fake_post(url, **kw):
            posts.append(kw["data"])
            return FakeResp(200, {"agent_run_id": "r1"})

        def fake_get(url, **kw):
            return FakeResp(200, {"status": "completed"}) if url.endswith("/r1") \
                else FakeResp(200, lines=[])

        config.SCIENCEPAL_MODEL = "platform::kimi-k3"
        with mock.patch.object(sciencepal.requests, "post", fake_post), \
                mock.patch.object(sciencepal.requests, "get", fake_get):
            with self.assertRaises(sciencepal.SciencePalError):
                sciencepal.run_agent("agent-1", "hi", timeout_s=15)
        self.assertEqual(posts[0]["model_name"], "platform::kimi-k3")

    def test_asked_is_error(self):
        with mock.patch.object(sciencepal.requests, "post",
                               lambda url, **kw: FakeResp(200, {"agent_run_id": "r1",
                                                                 "thread_id": "t1"})), \
                mock.patch.object(sciencepal.requests, "get",
                                  lambda url, **kw: FakeResp(200, lines=[])
                                  if url.endswith("/stream")
                                  else FakeResp(200, {"status": "asked"})):
            with self.assertRaises(sciencepal.SciencePalError) as ctx:
                sciencepal.run_agent("agent-1", "hi", timeout_s=15)
        msg = str(ctx.exception)
        self.assertIn("追问", msg)
        self.assertIn("thread_id=t1", msg)

    def test_429_is_actionable(self):
        with mock.patch.object(sciencepal.requests, "post",
                               lambda url, **kw: FakeResp(429, text="quota exceeded")):
            with self.assertRaises(sciencepal.SciencePalError) as ctx:
                sciencepal.run_agent("agent-1", "hi", timeout_s=15)
        self.assertIn("额度", str(ctx.exception))

    def test_401_is_actionable(self):
        with mock.patch.object(sciencepal.requests, "post",
                               lambda url, **kw: FakeResp(401, text="unauthorized")):
            with self.assertRaises(sciencepal.SciencePalError) as ctx:
                sciencepal.run_agent("agent-1", "hi", timeout_s=15)
        self.assertIn("API Key", str(ctx.exception))

    def test_empty_stream_falls_back_to_run_body(self):
        body = {"status": "completed",
                "messages": [{"role": "assistant", "content": "来自 run 的正文"}]}

        def fake_get(url, **kw):
            if url.endswith("/stream"):
                return FakeResp(200, lines=[])
            return FakeResp(200, body)

        with mock.patch.object(sciencepal.requests, "post",
                               lambda url, **kw: FakeResp(200, {"agent_run_id": "r1"})), \
                mock.patch.object(sciencepal.requests, "get", fake_get):
            out = sciencepal.run_agent("agent-1", "hi", timeout_s=15)
        self.assertEqual(out["answer"], "来自 run 的正文")

    def test_filter_requires_agent_id(self):
        config.SCIENCEPAL_FILTER_AGENT_ID = ""
        with mock.patch.object(sciencepal.requests, "get",
                               lambda *a, **k: FakeResp(200, {"agents": []})):
            with self.assertRaises(sciencepal.SciencePalError) as ctx:
                sciencepal.filter_papers({"text": "HCC"}, [{"pmid": "1"}])
        self.assertIn("SCIENCEPAL_FILTER_AGENT_ID", str(ctx.exception))

    def test_base_url_normalized(self):
        config.SCIENCEPAL_BASE_URL = "https://example.test/"
        seen = []

        def fake_post(url, **kw):
            seen.append(url)
            return FakeResp(200, {"agent_run_id": "r1"})

        with mock.patch.object(sciencepal.requests, "post", fake_post), \
                mock.patch.object(sciencepal.requests, "get",
                                  lambda url, **kw: FakeResp(200, lines=[])
                                  if url.endswith("/stream")
                                  else FakeResp(200, {"status": "completed"})):
            try:
                sciencepal.run_agent("agent-1", "hi", timeout_s=15)
            except sciencepal.SciencePalError:
                pass
        self.assertEqual(seen[0], "https://example.test/api/agent/initiate")


    # ---- 未配置 filter agent 时的自动兜底 ----
    def _discovery_get(self, agents):
        def fake_get(url, **kw):
            if "agent-catalog" in url or url.rstrip("/").endswith("/agents"):
                return FakeResp(200, {"agents": agents})
            if url.endswith("/stream"):
                return FakeResp(200, lines=sse({"type": "assistant",
                                                "content": '{"keep":[]}'}))
            return FakeResp(200, {"status": "completed"})
        return fake_get

    def test_auto_agent_single_usable(self):
        config.SCIENCEPAL_FILTER_AGENT_ID = ""
        posts = []

        def fake_post(url, **kw):
            posts.append(kw["data"])
            return FakeResp(200, {"agent_run_id": "r1"})

        with mock.patch.object(sciencepal.requests, "post", fake_post),                 mock.patch.object(sciencepal.requests, "get",
                                  self._discovery_get([{"agent_id": "a1",
                                                        "name": "HCC filter"}])):
            out = sciencepal.filter_papers({"text": "HCC"}, [{"pmid": "1"}])
        self.assertEqual(posts[0]["agent_id"], "a1")
        self.assertEqual(out["auto_agent"], "HCC filter")

    def test_auto_agent_prefers_name_match(self):
        config.SCIENCEPAL_FILTER_AGENT_ID = ""
        posts = []

        def fake_post(url, **kw):
            posts.append(kw["data"])
            return FakeResp(200, {"agent_run_id": "r1"})

        agents = [{"agent_id": "a1", "name": "Writer"},
                  {"agent_id": "a2", "name": "HCC 文献筛选器"},
                  {"agent_id": "a3", "name": "Coder"}]
        with mock.patch.object(sciencepal.requests, "post", fake_post),                 mock.patch.object(sciencepal.requests, "get",
                                  self._discovery_get(agents)):
            sciencepal.filter_papers({"text": "HCC"}, [{"pmid": "1"}])
        self.assertEqual(posts[0]["agent_id"], "a2")

    def test_auto_agent_ambiguous_degrades(self):
        config.SCIENCEPAL_FILTER_AGENT_ID = ""
        agents = [{"agent_id": "a1", "name": "Writer"},
                  {"agent_id": "a2", "name": "Coder"}]
        with mock.patch.object(sciencepal.requests, "get",
                               self._discovery_get(agents)):
            with self.assertRaises(sciencepal.SciencePalError) as ctx:
                sciencepal.filter_papers({"text": "HCC"}, [{"pmid": "1"}])
        self.assertIn("无法自动确定", str(ctx.exception))

    def test_env_agent_skips_discovery(self):
        config.SCIENCEPAL_FILTER_AGENT_ID = "env-agent"
        posts = []

        def fake_post(url, **kw):
            posts.append(kw["data"])
            return FakeResp(200, {"agent_run_id": "r1"})

        def fake_get(url, **kw):
            assert "agent-catalog" not in url, "配置了 env 就不该再发现 Agent"
            if url.endswith("/stream"):
                return FakeResp(200, lines=sse({"type": "assistant",
                                                "content": '{"keep":[]}'}))
            return FakeResp(200, {"status": "completed"})

        with mock.patch.object(sciencepal.requests, "post", fake_post),                 mock.patch.object(sciencepal.requests, "get", fake_get):
            sciencepal.filter_papers({"text": "HCC"}, [{"pmid": "1"}])
        self.assertEqual(posts[0]["agent_id"], "env-agent")


    def test_filter_empty_keep_falls_back(self):
        from hccfit import strategies
        papers = [{"pmid": "1", "title": "A"}, {"pmid": "2", "title": "B"}]
        base = {"papers": papers, "pool": papers,
                "meta": {"retrieval_ms": 1, "readability": {}}}
        with mock.patch.object(strategies, "fast_search", return_value=base),                 mock.patch.object(strategies, "filter_papers",
                                  return_value={"parsed": {"keep": []},
                                                "status": "completed", "latency_ms": 3}),                 mock.patch.object(strategies, "annotate_readability",
                                  side_effect=lambda ps: ps):
            out = strategies.run({"text": "HCC", "disease": "HCC"}, "balanced", 2, 150000)
        self.assertTrue(out["meta"]["degraded"])
        self.assertEqual([p["pmid"] for p in out["papers"]], ["1", "2"])
        self.assertTrue(any("回退算法" in w for w in out["warnings"]), out["warnings"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
