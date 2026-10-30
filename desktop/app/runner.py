# -*- coding: utf-8 -*-
"""桌面版运行编排：mode → brain/planner（与 Web 版 run_pipeline 等价，但不碰 HTTP）。"""
from __future__ import annotations

from app.paths import CORE  # noqa: F401  (确保 core 在 sys.path)

_MOCK = {"srv": None, "url": None}


def ensure_mock() -> str:
    """本地假 SciencePal（复刻 initiate/轮询/409）。只在 mock_sp 模式用。"""
    if _MOCK["srv"] is None:
        from mock_sciencepal import start, reset
        srv, url = start(0)
        reset(delay=1.0)
        _MOCK.update(srv=srv, url=url)
    return _MOCK["url"]


def build_planner(mode: str, workspace, scenario: dict, files, opts: dict, emit):
    from brains import ManualBrain, StubBrain
    from hcc_report import MODE as HCC_MODE
    from planner import HccPlanner, Planner
    from sciencepal import SciencePalBrain

    mode = (mode or "stub").lower()
    if mode == HCC_MODE:
        brain = SciencePalBrain(workspace, api_key=opts.get("api_key"), base=opts.get("base"),
                                agent_name=opts.get("agent_name") or "HCC-Fit",
                                timeout=int(opts.get("timeout") or 240), strict_agent=True)
        return HccPlanner(brain, str(workspace), scenario, emit=emit, files=files)

    if mode == "sciencepal":
        brain = SciencePalBrain(workspace, api_key=opts.get("api_key"), base=opts.get("base"),
                                agent_name=opts.get("agent_name") or "HCC-Fit",
                                timeout=int(opts.get("timeout") or 180))
        return Planner(brain, str(workspace), scenario, emit=emit, files=files)

    if mode == "mock_sp":
        brain = SciencePalBrain(workspace, api_key="mock-key", base=ensure_mock(),
                                agent_name=opts.get("agent_name") or "HCC-Fit", timeout=120)
        brain.mode_label = "mock_sp"
        return Planner(brain, str(workspace), scenario, emit=emit, files=files)

    if mode == "manual":
        return Planner(ManualBrain(workspace), str(workspace), scenario, emit=emit, files=files)

    return Planner(StubBrain(workspace), str(workspace), scenario, emit=emit, files=files)


def format_error(exc: BaseException) -> str:
    name = type(exc).__name__
    msg = str(exc)
    if name == "Pending":
        return "等待人工结果：%s\n把 JSON 写到这个路径后，再点一次开始。" % msg
    if "API Key" in msg or "401" in msg:
        return msg
    if "Failed to fetch" in msg or "连不上" in msg:
        return msg
    return "%s: %s" % (name, msg) if msg else name
