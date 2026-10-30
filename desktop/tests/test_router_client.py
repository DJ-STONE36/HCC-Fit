# -*- coding: utf-8 -*-
"""Router 客户端映射测试（不联网）。"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.input_normalization import RuleExtractor  # noqa: E402
from app.router_client import RouterClient, RouterError, from_session  # noqa: E402


def main():
    rules = RuleExtractor(BASE / "resources" / "terms.csv")
    se = rules.extract("HCC，准备肝切除，预测从手术日起一年复发")
    sc = RuleExtractor.to_agent_scenario(se)
    payload = from_session(se, sc)
    assert payload["disease"] == "HCC"
    assert payload["time_zero"] == "surgery"
    assert payload["outcome"] == "RFS"
    assert payload["horizon_months"] == 12

    icca = rules.extract("肝内胆管癌术后，想看三年OS")
    try:
        from_session(icca, RuleExtractor.to_agent_scenario(icca))
    except RouterError:
        pass
    else:
        raise AssertionError("iCCA 应被 Router 拒绝")

    c = RouterClient("http://example.test", "t")
    c._post = lambda payload: {"papers": [], "strategy_used": "fast"}
    assert c.search(payload, "fast", 8)["strategy_used"] == "fast"

    import urllib.request
    captured = {}

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"papers": [], "strategy_used": "balanced"}'

    def fake_urlopen(req, timeout=0):
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        return Resp()

    orig = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        RouterClient("http://example.test", "t", sciencepal_key="sp-demo").search(payload)
    finally:
        urllib.request.urlopen = orig
    assert captured["headers"].get("x-sciencepal-api-key") == "sp-demo"

    # 10061：目标端口没有服务在监听 → 要给出可操作提示，而不是裸 URLError
    import urllib.error

    def refuse(_req, timeout=0):
        raise urllib.error.URLError(ConnectionRefusedError(10061, "由于目标计算机积极拒绝，无法连接。"))

    urllib.request.urlopen = refuse
    try:
        try:
            RouterClient("http://127.0.0.1:8080").search(payload, "fast", 3)
        except RouterError as exc:
            msg = str(exc)
            assert "连不上" in msg and "内置引擎" in msg, msg
        else:
            raise AssertionError("端口未监听时应报连接错误")
    finally:
        urllib.request.urlopen = orig

    print("PASS router client mapping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
