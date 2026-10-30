# -*- coding: utf-8 -*-
"""输入会话的落盘/读取（P1 UI 使用）。"""
from __future__ import annotations

from pathlib import Path

from .input_normalization import InputSession
from . import paths


def save(session: InputSession, directory=None) -> Path:
    base = Path(directory) if directory else paths.inputs_dir()
    path = base / ("%s.json" % session.input_id)
    session.save(path)
    return path


def load(input_id: str, directory=None) -> InputSession:
    base = Path(directory) if directory else paths.inputs_dir()
    return InputSession.load(base / ("%s.json" % input_id))


def list_sessions(directory=None):
    base = Path(directory) if directory else paths.inputs_dir()
    return sorted(base.glob("in_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
