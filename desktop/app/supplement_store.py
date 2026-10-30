# -*- coding: utf-8 -*-
"""补充资料落盘。"""
from __future__ import annotations

from pathlib import Path

from .supplement import Supplement
from . import paths


def save(sup: Supplement, directory=None) -> Path:
    base = Path(directory) if directory else paths.supplements_dir()
    path = base / ("%s.json" % sup.supplement_id)
    sup.save(path)
    return path


def load(supplement_id: str, directory=None) -> Supplement:
    base = Path(directory) if directory else paths.supplements_dir()
    return Supplement.load(base / ("%s.json" % supplement_id))


def list_supplements(directory=None):
    base = Path(directory) if directory else paths.supplements_dir()
    return sorted(base.glob("sup_*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
