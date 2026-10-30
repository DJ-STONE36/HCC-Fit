# -*- coding: utf-8 -*-
"""内置 Router 引擎测试（不联网）：路径解析 / 状态 / 参数透传 / 错误映射 / 对话框。"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import router_engine  # noqa: E402
from app.router_client import RouterWorker  # noqa: E402

SCENARIO = {"text": "HCC 切除后想预测 1 年复发", "disease": "HCC",
            "time_zero": "surgery", "outcome": "RFS", "horizon_months": 12}

RESULTS = []


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append(True)
        print("PASS  %-38s %s" % (name, detail or ""))
    except Exception as exc:  # noqa: BLE001
        RESULTS.append(False)
        print("FAIL  %-38s %s: %s" % (name, type(exc).__name__, exc))


def case_engine_available():
    st = router_engine.engine_status()
    assert st["available"], st
    path = Path(st["path"])
    assert (path / "hccfit" / "strategies.py").is_file(), path
    assert st["strategies"] == ["fast", "balanced", "auto"], st["strategies"]
    assert router_engine.find_router_dir() is not None
    return "引擎可用：%s" % path.name


def case_search_passthrough():
    import hccfit.strategies as strategies
    captured = {}
    orig = strategies.run

    def fake(scenario, strategy="auto", k=5, deadline_ms=120000, thinking=False,
             require_fulltext=False, request_id=None, api_key=None):
        captured.update(scenario=dict(scenario), strategy=strategy, k=k,
                        deadline_ms=deadline_ms, api_key=api_key)
        return {"papers": [], "strategy_used": strategy, "meta": {}}

    strategies.run = fake
    try:
        out = router_engine.search(SCENARIO, strategy="fast", k=99,
                                   sciencepal_key="sp-x", filter_agent_id="agent-1")
        router_engine.search(SCENARIO, strategy="fast", k=0, sciencepal_key="sp-x")
    finally:
        strategies.run = orig
    assert out["strategy_used"] == "fast", out
    assert captured["k"] == 1, captured          # k 被夹到 1..8
    assert captured["api_key"] == "sp-x", captured
    from hccfit import config
    assert config.SCIENCEPAL_FILTER_AGENT_ID == "agent-1"
    return "scenario/strategy/k/api_key 透传；k 夹到 1..8"


def case_scope_error():
    import hccfit.strategies as strategies
    from hccfit.scope import ScopeError
    orig = strategies.run

    def boom(*_a, **_k):
        raise ScopeError("out_of_scope", "只接受已确认 HCC")

    strategies.run = boom
    try:
        try:
            router_engine.search({"text": "肝内胆管癌", "disease": "iCCA"}, "fast", 3)
        except router_engine.RouterEngineError as exc:
            assert "out_of_scope" in str(exc), exc
        else:
            raise AssertionError("越界诊断应抛 RouterEngineError")
    finally:
        strategies.run = orig
    return "ScopeError → RouterEngineError（带 code）"


def case_worker_builtin():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    calls = {}
    orig = router_engine.search

    def fake(scenario, strategy="fast", k=8, sciencepal_key="", filter_agent_id="",
             base_url="", deadline_ms=None):
        calls.update(strategy=strategy, k=k, key=sciencepal_key,
                     agent=filter_agent_id)
        return {"papers": [{"pmid": "1"}], "strategy_used": strategy}

    router_engine.search = fake
    got = []
    worker = RouterWorker(SCENARIO, "balanced", 2, "sp-k", engine="builtin",
                          filter_agent_id="agent-9")
    worker.done.connect(got.append)
    try:
        worker.run()
    finally:
        router_engine.search = orig
    assert got and got[0]["papers"], got
    assert calls == {"strategy": "balanced", "k": 2, "key": "sp-k",
                     "agent": "agent-9"}, calls
    return "后台线程走内置引擎，参数不出错"


def case_worker_http():
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    import app.router_client as rc
    captured = {}

    class FakeClient:
        def __init__(self, base_url, token, sciencepal_key=""):
            captured.update(url=base_url, token=token, key=sciencepal_key)

        def search(self, scenario, strategy, k):
            captured.update(strategy=strategy, k=k)
            return {"papers": [], "strategy_used": strategy}

    orig = rc.RouterClient
    rc.RouterClient = FakeClient
    got = []
    worker = RouterWorker(SCENARIO, "fast", 4, engine="http",
                          base_url="http://127.0.0.1:8080", token="tok")
    worker.done.connect(got.append)
    try:
        worker.run()
    finally:
        rc.RouterClient = orig
    assert captured["url"] == "http://127.0.0.1:8080" and captured["token"] == "tok"
    assert got and got[0]["strategy_used"] == "fast", got
    return "外部 HTTP 核心代码仍保留（界面不暴露）"


def case_dialog():
    from PySide6.QtWidgets import QApplication
    from app.router_dialog import RouterDialog
    QApplication.instance() or QApplication([])
    dlg = RouterDialog(SCENARIO, "sp_live_abcdefghijklmnop")
    assert not hasattr(dlg, "engine"), "界面不应再有检索方式下拉框"
    assert not hasattr(dlg, "external_row"), "界面不应再有外部服务地址栏"
    assert not hasattr(dlg, "filter_agent"), "界面不应再要求 filter agent ID"
    assert "内置引擎可用" in dlg.engine_msg.text(), dlg.engine_msg.text()
    assert "已配置" in dlg.engine_msg.text(), dlg.engine_msg.text()
    assert dlg.search_btn.isEnabled()
    dlg.close()
    return "只有内置引擎；Key 来自主窗口；无 filter agent 输入"


def main():
    check("内置引擎可用", case_engine_available)
    check("检索参数透传", case_search_passthrough)
    check("越界诊断报错", case_scope_error)
    check("后台线程：内置引擎", case_worker_builtin)
    check("后台线程：外部 HTTP", case_worker_http)
    check("对话框只有内置引擎", case_dialog)
    ok = sum(1 for r in RESULTS if r)
    print("\n%d/%d 通过" % (ok, len(RESULTS)))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
