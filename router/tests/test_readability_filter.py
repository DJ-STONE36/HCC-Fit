# -*- coding: utf-8 -*-
"""require_fulltext（只看 PMC 免费全文）过滤测试。"""
import unittest
from unittest import mock

from hccfit import strategies


def _papers(n=5):
    return [{"pmid": str(i), "title": "t%d" % i} for i in range(n)]


def _annotate(papers):
    out = []
    for i, p in enumerate(papers):
        lvl = "fulltext" if i % 2 == 0 else "abstract_only"
        q = dict(p)
        q["readability"] = {
            "level": lvl,
            "pmcid": "PMC%d" % i if lvl == "fulltext" else "",
            "url": "https://example.test/%s" % p.get("pmid"),
            "full_text": lvl == "fulltext",
        }
        out.append(q)
    return out


class ReadabilityFilterTests(unittest.TestCase):
    def _run(self, require_fulltext, k=3):
        seen = {}

        def fake_fast(scenario, k=5, require_fulltext=False):
            seen["fetch_k"] = k
            return {"papers": _papers(k), "pool": [],
                    "meta": {"retrieval_ms": 1, "readability": {}}}

        with mock.patch.object(strategies, "fast_search", fake_fast), \
             mock.patch.object(strategies, "annotate_readability", _annotate):
            res = strategies.run({"text": "HCC"}, strategy="fast", k=k,
                                 require_fulltext=require_fulltext)
        return res, seen

    def test_require_fulltext_keeps_only_pmc(self):
        res, seen = self._run(True)
        self.assertEqual(seen["fetch_k"], 12)          # 多取，过滤后仍够 k
        self.assertEqual(len(res["papers"]), 3)
        self.assertTrue(all(p["readability"]["level"] == "fulltext"
                            for p in res["papers"]))
        self.assertTrue(any("require_fulltext" in w for w in res["warnings"]))
        self.assertEqual(res["meta"]["readability_counts"]["abstract_only"], 0)

    def test_without_filter_keeps_mixed(self):
        res, seen = self._run(False)
        self.assertEqual(seen["fetch_k"], 3)
        self.assertEqual(len(res["papers"]), 3)
        levels = {p["readability"]["level"] for p in res["papers"]}
        self.assertIn("abstract_only", levels)
        self.assertFalse(any("require_fulltext" in w for w in res["warnings"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
