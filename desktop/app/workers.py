# -*- coding: utf-8 -*-
"""后台线程：所有网络/耗时操作都不在 UI 线程。"""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot

from app.runner import build_planner, format_error


class RunWorker(QObject):
    stage = Signal(dict)
    done = Signal(dict)
    failed = Signal(str)

    def __init__(self, mode, workspace, scenario, files, opts):
        super().__init__()
        self.mode = mode
        self.workspace = workspace
        self.scenario = scenario
        self.files = files
        self.opts = opts

    @Slot()
    def run(self):
        try:
            planner = build_planner(self.mode, self.workspace, self.scenario,
                                    self.files, self.opts, self.stage.emit)
            self.done.emit(planner.run())
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(format_error(exc))


class ProbeWorker(QObject):
    result = Signal(bool, str, list)

    def __init__(self, workspace, api_key, base, agent_name):
        super().__init__()
        self.workspace = workspace
        self.api_key = api_key
        self.base = base
        self.agent_name = agent_name

    @Slot()
    def run(self):
        try:
            from sciencepal import SciencePalBrain
            brain = SciencePalBrain(self.workspace, api_key=self.api_key, base=self.base,
                                    agent_name=self.agent_name or "HCC-Fit", timeout=30)
            ok, msg, names = brain.probe()
            self.result.emit(bool(ok), str(msg), list(names or []))
        except Exception as exc:  # noqa: BLE001
            self.result.emit(False, format_error(exc), [])
