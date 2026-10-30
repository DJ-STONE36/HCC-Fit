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
    print("PASS router client mapping")
    return 0


if __name__ == "__main__":
    sys.exit(main())
