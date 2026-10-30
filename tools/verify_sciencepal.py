#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 SciencePal Key / filter agent / balanced 链路（**不会打印 Key**）。

用法：
    python tools/verify_sciencepal.py --env D:\\HCC-Fit\\_secrets\\sciencepal.env
    python tools/verify_sciencepal.py --env ... --k 3 --skip-balanced

env 文件格式（每行 KEY=VALUE，# 开头是注释）：
    SCIENCEPAL_API_KEY=sp_live_xxx
    SCIENCEPAL_FILTER_AGENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
    # 可选
    SCIENCEPAL_MODEL=
    SCIENCEPAL_BASE_URL=https://sciencepal.ai

退出码：0 成功；2 env 缺失；3 Key/401；4 balanced 失败。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "router"))


def load_env(path) -> dict:
    """容忍 BOM / export 前缀 / 引号 / CRLF。"""
    data = {}
    text = Path(path).read_text(encoding="utf-8-sig", errors="replace")
    for line in text.splitlines():
        line = line.strip()
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        data[key.strip()] = value.strip().strip('"').strip("'").strip()
    return data


def mask(secret: str) -> str:
    s = secret or ""
    if len(s) <= 10:
        return "（空或过短）"
    return "%s…%s（%d 位）" % (s[:4], s[-4:], len(s))


SCENARIO = {
    "text": "58 岁男性，HBV 相关 HCC，根治性切除后，想预测 1 年内复发",
    "disease": "HCC",
    "time_zero": "surgery",
    "treatment_state": "curative resection",
    "outcome": "RFS",
    "horizon_months": 12,
}

CANDIDATES = [
    {"pmid": "37850181", "year": 2023, "journal": "Eur Radiol",
     "title": "Deep Segmentation Feature-Based Radiomics Improves Recurrence Prediction "
              "in Early-Stage Hepatocellular Carcinoma After Curative Resection",
     "abstract_snippet": "Multicenter retrospective study of 612 patients undergoing curative "
                         "resection for HCC; radiomics model predicting 1-year recurrence, "
                         "C-index 0.78 in external validation.",
     "why": ["结局/时点匹配"], "risk": ["回顾性"]},
    {"pmid": "35296018", "year": 2021, "journal": "Ann Surg Oncol",
     "title": "Nomograms for Predicting Hepatocellular Carcinoma Recurrence and Overall "
              "Survival After Liver Transplantation",
     "abstract_snippet": "Single-center cohort of 401 liver transplant recipients; nomogram "
                         "for post-transplant recurrence and overall survival.",
     "why": ["有列线图"], "risk": ["人群是移植，不是切除"]},
    {"pmid": "28500000", "year": 2017, "journal": "J Hepatol",
     "title": "Molecular mechanisms of hepatocellular carcinoma: a narrative review",
     "abstract_snippet": "Review of signaling pathways and tumor biology; no prediction model.",
     "why": [], "risk": ["综述，非预测模型"]},
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", required=True, help="包含 SCIENCEPAL_API_KEY 的 env 文件")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--skip-balanced", action="store_true", help="只验证 Key/Agent，不跑 LLM")
    ap.add_argument("--skip-models", action="store_true")
    args = ap.parse_args()

    env = load_env(args.env)
    key = env.get("SCIENCEPAL_API_KEY", "").strip()
    agent = env.get("SCIENCEPAL_FILTER_AGENT_ID", "").strip()
    if not key:
        print("FAIL  env 文件里没有 SCIENCEPAL_API_KEY")
        return 2

    for name in ("SCIENCEPAL_API_KEY", "SCIENCEPAL_FILTER_AGENT_ID",
                 "SCIENCEPAL_BASE_URL", "SCIENCEPAL_MODEL"):
        if env.get(name):
            os.environ[name] = env[name].strip()

    from hccfit import config, sciencepal
    import requests

    print("Key   :", mask(key))
    print("Base  :", config.SCIENCEPAL_BASE_URL)
    print("Agent :", agent or "（未指定，将自动发现）")
    print("Model :", config.SCIENCEPAL_MODEL or "（平台默认）")
    print("-" * 60)

    # 1) Key + Agent 列表
    try:
        resp = requests.get(sciencepal._base() + "/agent-catalog?usable_only=true",
                            headers=sciencepal._headers(key), timeout=20)
    except Exception as exc:  # noqa: BLE001
        print("FAIL  连不上 SciencePal：%s: %s" % (type(exc).__name__, exc))
        return 3
    if resp.status_code == 401:
        print("FAIL  401：Key 无效、过期或未授权")
        return 3
    if resp.status_code >= 400:
        print("WARN  agent-catalog HTTP %s（旧后端，继续尝试 /agents）" % resp.status_code)
        agents = []
    else:
        agents = (resp.json() or {}).get("agents") or []
        print("可用 Agent：%d 个" % len(agents))
        for ag in agents[:20]:
            aid = str(ag.get("agent_id") or ag.get("id") or "")
            mark = "  <== 指定" if agent and aid == agent else ""
            print("  - %s | %s | %s%s" % (aid, ag.get("name"), ag.get("source"), mark))
        if agent and agents and not any(
                str(ag.get("agent_id") or ag.get("id") or "") == agent for ag in agents):
            print("WARN  指定的 agent id 不在可用列表里（可能无权限或写错）")

    # 2) 模型列表（可选）
    if not args.skip_models:
        try:
            r2 = requests.get(sciencepal._base() + "/billing/available-models",
                              headers=sciencepal._headers(key), timeout=20)
            if r2.status_code < 400:
                models = (r2.json() or {}).get("models") or []
                ids = [m.get("id") for m in models if isinstance(m, dict)]
                print("可用模型：%d 个，前 5 个：%s" % (len(ids), "、".join(map(str, ids[:5]))))
            else:
                print("WARN  模型列表 HTTP %s（不影响默认模型）" % r2.status_code)
        except Exception as exc:  # noqa: BLE001
            print("WARN  模型列表查询失败：%s" % type(exc).__name__)

    if args.skip_balanced:
        print("\nOK    Key/Agent 检查完成（已跳过 balanced）")
        return 0

    # 3) balanced：真实调用一次 filter agent
    print("-" * 60)
    print("调用 balanced filter（会消耗一次 Agent 运行，k=%d）…" % args.k)
    try:
        res = sciencepal.filter_papers(SCENARIO, CANDIDATES,
                                       timeout_s=90, api_key=key)
    except Exception as exc:  # noqa: BLE001
        print("FAIL  balanced：%s: %s" % (type(exc).__name__, exc))
        return 4

    parsed = res.get("parsed")
    print("status=%s  latency=%sms  run=%s  thread=%s"
          % (res.get("status"), res.get("latency_ms"), res.get("run_id"), res.get("thread_id")))
    if res.get("auto_agent"):
        print("auto agent:", res["auto_agent"])
    print("answer 前 400 字：", (res.get("answer") or "")[:400].replace("\n", " "))
    if not parsed:
        print("FAIL  回答里没有可解析的 JSON（balanced 会判定为降级）")
        return 4
    print("keep：", json.dumps(parsed.get("keep"), ensure_ascii=False))
    print("drop：", json.dumps(parsed.get("drop"), ensure_ascii=False))
    print("\nOK    Key + Agent + balanced 全部可用")
    return 0


if __name__ == "__main__":
    sys.exit(main())
