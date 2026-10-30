# -*- coding: utf-8 -*-
"""桌面 SciencePal 客户端：终态/追问/错误码（离线，mock 掉 HTTP）。"""
import os
import shutil
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))
sys.path.insert(0, str(BASE / "core"))

from sciencepal import SciencePalBrain, BrainError  # noqa: E402

RESULTS = []


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append(True)
        print("PASS  %-38s %s" % (name, detail or ""))
    except Exception as exc:  # noqa: BLE001
        RESULTS.append(False)
        print("FAIL  %-38s %s: %s" % (name, type(exc).__name__, exc))


def _brain():
    ws = BASE / "tests" / "_tmp_sp"
    shutil.rmtree(ws, ignore_errors=True)
    ws.mkdir(parents=True, exist_ok=True)
    return SciencePalBrain(str(ws), api_key="sp-test", timeout=5), ws


def case_asked():
    brain, ws = _brain()
    try:
        brain._json = lambda *a, **k: (200, {"status": "asked"})
        try:
            brain._poll("run-1", "thread-1")
        except BrainError as exc:
            assert "追问" in str(exc), exc
            assert "thread-1" in str(exc), exc
        else:
            raise AssertionError("asked 应报错")
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    return "asked → 明确报错（不再空等）"


def case_failed():
    brain, ws = _brain()
    try:
        brain._json = lambda *a, **k: (200, {"status": "failed", "error": "boom"})
        try:
            brain._poll("run-2")
        except BrainError as exc:
            assert "失败" in str(exc) and "boom" in str(exc), exc
        else:
            raise AssertionError("failed 应报错")
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    return "failed → 带平台 error"


def case_http_hints():
    brain, ws = _brain()
    try:
        assert "API Key" in brain._http_hint("initiate", 401, {})
        assert "权限" in brain._http_hint("initiate", 403, {})
        assert "额度" in brain._http_hint("initiate", 429, {})
        assert "模型" in brain._http_hint("initiate", 422, {})
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    return "401/403/422/429 均有可操作提示"


def case_initiate_params():
    import json as _json
    brain, ws = _brain()
    try:
        sent = {}

        def fake_req(method, path, data=None, headers=None, timeout=None):
            if path == "/api/agents":
                return 200, _json.dumps({"agents": [{"name": "HCC-Fit", "id": "aid-1"}]})
            if path == "/api/agent/initiate":
                sent["body"] = data or b""
                return 200, _json.dumps({"thread_id": "t1", "agent_run_id": "r1"})
            return 200, "{}"

        brain._req = fake_req
        brain._initiate("hello", [])
        body = sent["body"]
        assert b'name="agent_id"' in body and b"aid-1" in body, body[:200]
        assert b'name="agent_select_type"' in body and b"manual" in body, body[:200]
        assert b"hello" in body, body[:200]
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    return "initiate 带 agent_id / agent_select_type"


def main():
    check("追问 asked", case_asked)
    check("失败终态", case_failed)
    check("HTTP 错误提示", case_http_hints)
    check("initiate 参数", case_initiate_params)
    ok = sum(1 for r in RESULTS if r)
    print("\n%d/%d 通过" % (ok, len(RESULTS)))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
