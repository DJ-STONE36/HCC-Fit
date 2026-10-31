# -*- coding: utf-8 -*-
"""桌面版运行编排：mode → brain/planner（与 Web 版 run_pipeline 等价，但不碰 HTTP）。

界面只提供 sciencepal / sciencepal_hcc；`stub` 仅保留给自动化测试（离线、确定）。
"""
from __future__ import annotations

from app.paths import CORE  # noqa: F401  (确保 core 在 sys.path)


def build_planner(mode: str, workspace, scenario: dict, files, opts: dict, emit):
    from brains import BrainError, StubBrain
    from hcc_report import MODE as HCC_MODE
    from planner import HccPlanner, Planner
    from sciencepal import SciencePalBrain

    mode = (mode or "sciencepal").lower()
    atts = [a for a in (opts.get("attachments") or []) if str(a).strip()]  # 补充资料附件
    if mode == HCC_MODE:
        brain = SciencePalBrain(workspace, api_key=opts.get("api_key"), base=opts.get("base"),
                                agent_name=opts.get("agent_name") or "HCC-Fit",
                                timeout=int(opts.get("timeout") or 240), strict_agent=True)
        return HccPlanner(brain, str(workspace), scenario, emit=emit, files=files,
                          split_papers=bool(opts.get("split_papers")), attachments=atts)

    if mode == "sciencepal":
        brain = SciencePalBrain(workspace, api_key=opts.get("api_key"), base=opts.get("base"),
                                agent_name=opts.get("agent_name") or "HCC-Fit",
                                timeout=int(opts.get("timeout") or 180))
        return Planner(brain, str(workspace), scenario, emit=emit, files=files,
                       split_papers=bool(opts.get("split_papers")), attachments=atts)

    # 仅自动化测试使用：界面不提供离线演示模式
    if mode == "stub":
        return Planner(StubBrain(workspace), str(workspace), scenario, emit=emit, files=files,
                       split_papers=bool(opts.get("split_papers")), attachments=atts)

    # 旧报告里可能残留已移除的模式（manual / mock_sp）：明确报错，不静默产出演示数据
    raise BrainError("该大脑模式已移除：%s；请在“3 · 大脑”里选择 SciencePal 模式"
                     % mode)


def format_error(exc: BaseException) -> str:
    name = type(exc).__name__
    msg = str(exc)
    if name == "Pending":
        return "等待人工结果：%s" % msg
    if "API Key" in msg or "401" in msg:
        return msg
    if "Failed to fetch" in msg or "连不上" in msg:
        return msg
    return "%s: %s" % (name, msg) if msg else name
