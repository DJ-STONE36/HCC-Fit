# -*- coding: utf-8 -*-
"""API 回归：不依赖网络。"""
import unittest

from fastapi.testclient import TestClient

import hccfit.app as app_module
import hccfit.config as config_module
import hccfit.strategies as strategies_module
from hccfit.core import build_plan
from hccfit.scope import ScopeError, normalize_disease
from hccfit.sciencepal import SciencePalError, filter_papers


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.token = app_module.ROUTER_TOKEN
        app_module.ROUTER_TOKEN = ""
        self.client = TestClient(app_module.app)

    def tearDown(self):
        app_module.ROUTER_TOKEN = self.token

    def test_health(self):
        r = self.client.get("/healthz")
        self.assertEqual(r.status_code, 200)
        self.assertIn("enabled_strategies", r.json())
        self.assertIn("sciencepal_filter_configured", r.json())

    def test_strategies_no_deep(self):
        r = self.client.get("/v1/strategies")
        self.assertEqual(r.status_code, 200)
        ids = {x["id"] for x in r.json()["strategies"]}
        self.assertEqual(ids, {"fast", "balanced", "auto"})

    def test_deep_rejected(self):
        r = self.client.post("/v1/search", json={
            "scenario": {"text": "HCC 术后 3年 总生存", "disease": "HCC"},
            "strategy": "deep", "k": 5,
        })
        self.assertEqual(r.status_code, 400)
        self.assertIn("deep", r.json()["detail"])

    def test_k_over_8_rejected(self):
        r = self.client.post("/v1/search", json={
            "scenario": {"text": "HCC 术后 3年 总生存", "disease": "HCC"},
            "strategy": "fast", "k": 9,
        })
        self.assertEqual(r.status_code, 422)

    def test_non_hcc_rejected(self):
        for text, code in [
            ("肝内胆管癌术后，想看3年OS", "out_of_scope"),
            ("结肠癌肝转移，想看3年OS", "out_of_scope"),
            ("肝癌，类型未明确，想看3年OS", "ambiguous_disease"),
        ]:
            r = self.client.post("/v1/search", json={
                "scenario": {"text": text}, "strategy": "fast", "k": 3,
            })
            self.assertEqual(r.status_code, 400, text)
            self.assertEqual(r.json()["detail"]["code"], code, text)

    def test_structured_fields_drive_queries(self):
        plan = build_plan({
            "text": "患者情况", "disease": "HCC",
            "outcome": "PHLF", "time_zero": "surgery", "horizon_months": 3,
        })
        q = plan["queries"][0]
        self.assertIn("liver failure", q)
        self.assertIn("hepatectomy", q)
        self.assertNotIn("overall survival", q)
        self.assertEqual(plan["endpoint"], "PHLF")
        self.assertEqual(plan["horizon_months"], 3)

    def test_scope_function(self):
        self.assertEqual(normalize_disease({"text": "肝细胞癌术后"}), "HCC")
        self.assertEqual(normalize_disease({"text": "患者情况", "disease": "HCC"}), "HCC")
        with self.assertRaises(ScopeError):
            normalize_disease({"text": "肝内胆管癌术后"})
        with self.assertRaises(ScopeError):
            normalize_disease({"text": "肝癌，类型未明确"})

    def test_contract_why_risk_arrays(self):
        orig = strategies_module.run

        def fake(*args, **kwargs):
            return {
                "request_id": "x", "strategy_requested": "auto",
                "strategy_used": "fast", "scenario": {},
                "papers": [{"pmid": "1", "why": ["终点含 OS"], "risk": ["缺验证"]}],
                "meta": {}, "warnings": [], "next_actions": [],
            }

        strategies_module.run = fake
        try:
            r = self.client.post("/v1/search", json={
                "scenario": {"text": "HCC 术后", "disease": "HCC"},
                "strategy": "auto", "k": 5,
            })
        finally:
            strategies_module.run = orig
        self.assertEqual(r.status_code, 200)
        p = r.json()["papers"][0]
        self.assertIsInstance(p["why"], list)
        self.assertIsInstance(p["risk"], list)

    def test_missing_filter_agent_is_explicit(self):
        old = config_module.SCIENCEPAL_FILTER_AGENT_ID
        config_module.SCIENCEPAL_FILTER_AGENT_ID = ""
        try:
            with self.assertRaises(SciencePalError):
                filter_papers({"text": "HCC 术后"}, [])
        finally:
            config_module.SCIENCEPAL_FILTER_AGENT_ID = old

    def test_cors_allowlist(self):
        r = self.client.options("/v1/search", headers={
            "Origin": "http://localhost:3000",
            "Access-Control-Request-Method": "POST",
        })
        self.assertEqual(r.headers.get("access-control-allow-origin"), "http://localhost:3000")
        r2 = self.client.options("/v1/search", headers={
            "Origin": "http://evil.example",
            "Access-Control-Request-Method": "POST",
        })
        self.assertNotEqual(r2.headers.get("access-control-allow-origin"), "http://evil.example")

    def test_router_token(self):
        app_module.ROUTER_TOKEN = "secret"
        orig = strategies_module.run

        def fake(*args, **kwargs):
            return {"request_id": "x", "strategy_requested": "fast",
                    "strategy_used": "fast", "scenario": {}, "papers": [],
                    "meta": {}, "warnings": [], "next_actions": []}

        strategies_module.run = fake
        body = {"scenario": {"text": "HCC 术后", "disease": "HCC"},
                "strategy": "fast", "k": 3}
        try:
            r = self.client.post("/v1/search", json=body)
            self.assertEqual(r.status_code, 401)
            r = self.client.post("/v1/search", json=body,
                                 headers={"Authorization": "Bearer secret"})
            self.assertEqual(r.status_code, 200)
        finally:
            strategies_module.run = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
