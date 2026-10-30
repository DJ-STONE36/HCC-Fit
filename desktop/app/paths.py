# -*- coding: utf-8 -*-
"""路径、工作区、文件名工具（桌面版专用）。"""
from __future__ import annotations

import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QStandardPaths

BASE = Path(__file__).resolve().parent.parent
CORE = BASE / "core"
RESOURCES = BASE / "resources"
if str(CORE) not in sys.path:
    sys.path.insert(0, str(CORE))

SID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def _probe_writable(path: Path) -> bool:
    """真正写一个探针文件，确认目录可写（mkdir 成功不等于能写）。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".hccfit_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _data_candidates() -> list[Path]:
    override = (os.environ.get("HCCFIT_DATA_DIR") or "").strip()
    if override:
        return [Path(override)]
    out: list[Path] = []
    qt = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    if qt:
        out.append(Path(qt))
    local = (os.environ.get("LOCALAPPDATA") or "").strip()
    if local:
        out.append(Path(local) / "HCC-Fit")
    out.append(Path.home() / ".hcc-fit")
    return out


def app_data_dir() -> Path:
    """数据目录：HCCFIT_DATA_DIR 覆盖；否则按候选顺序找第一个**真正可写**的目录。

    注意：受限/沙盒进程里 `QStandardPaths` 可能给到 AppData\\LocalLow（存在但不可写），
    只 mkdir 会误判，所以这里用探针文件确认；都失败才退回系统临时目录。
    """
    for base in _data_candidates():
        if _probe_writable(base):
            return base
    import tempfile
    fallback = Path(tempfile.gettempdir()) / "HCC-Fit"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def new_sid() -> str:
    return time.strftime("%Y%m%d") + "-" + uuid.uuid4().hex[:8]


def inputs_dir() -> Path:
    """输入会话目录：与工作区同级，供审计/重载。"""
    p = app_data_dir() / "inputs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def supplements_dir() -> Path:
    """报告后补充资料目录。"""
    p = app_data_dir() / "supplements"
    p.mkdir(parents=True, exist_ok=True)
    return p


def workspace(sid: str) -> Path:
    if not SID_RE.match(sid or ""):
        raise ValueError("非法 sid")
    ws = app_data_dir() / "workspaces" / sid
    (ws / "files").mkdir(parents=True, exist_ok=True)
    return ws


def safe_name(name: str) -> str:
    """保留中文、字母、数字、点、下划线、短横；其余替换成 _。"""
    name = os.path.basename(name or "file")
    name = name.replace("\x00", "").strip()
    cleaned = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", name, flags=re.UNICODE)
    return cleaned[:120] or "file"


def stage_files(paths, ws: Path):
    """把用户选的文件复制到 workspace/files，返回落盘名列表。"""
    fdir = ws / "files"
    fdir.mkdir(parents=True, exist_ok=True)
    names = []
    for src in paths:
        src = Path(src)
        if not src.is_file():
            continue
        name = safe_name(src.name)
        if name in (".", ".."):
            continue
        target = fdir / name
        if target.exists():
            stem, suffix = os.path.splitext(name)
            i = 2
            while (fdir / ("%s_%d%s" % (stem, i, suffix))).exists():
                i += 1
            target = fdir / ("%s_%d%s" % (stem, i, suffix))
        shutil.copy2(src, target)
        names.append(target.name)
    return names


def resource(*parts) -> Path:
    return RESOURCES.joinpath(*parts)
