# -*- coding: utf-8 -*-
"""内置 Router 引擎：在桌面进程内直接调用 `hccfit.strategies.run`。

不需要单独启动 uvicorn、不占 8080、不需要 HTTP。开发态用仓库里的 `router/`；
打包态由 `HCC-Fit.spec` 把 `hccfit` 及其 toolkit 一起冻进程序（含 stdlib 依赖 xml.etree 等），
启动即用；`_MEIPASS/router` 只是保留的绿色版/外置源码兜底。

查找顺序：
1. 环境变量 `HCCFIT_ROUTER_DIR` —— 显式覆盖，开发/打包都生效；
2. 开发态：`desktop/../router`（与本包 `router/` 同级）→ `cwd/router` → `desktop/router`；
3. 打包态：默认用冻进 exe 的模块；`_MEIPASS/router` 只作为外置源码兜底
   （避免 exe 旁边恰好有旧 `router/` 时被顶替）。

只读：不修改 Router 源码；SciencePal 的 Key / Base / filter agent ID 只放内存。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 提示：这是桌面进程内的直连模式，失败信息要能指导排错，所以单独定义异常。
class RouterEngineError(RuntimeError):
    pass


_MODULE = None
_PATH: Path | None = None


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _candidates() -> list[Path]:
    here = Path(__file__).resolve().parent          # desktop/app
    desktop = here.parent                            # desktop
    out: list[Path] = []
    env = (os.environ.get("HCCFIT_ROUTER_DIR") or "").strip()
    if env:
        # 显式覆盖（热修/绿色版）优先，打包态也允许
        return [Path(env)]
    if _is_frozen():
        # 打包态默认只用冻进 exe 的模块，避免被 exe 旁边的旧 router/ 源码覆盖
        meipass = getattr(sys, "_MEIPASS", "")
        return [Path(meipass) / "router"] if meipass else []
    out += [
        desktop.parent / "router",                   # 包内同级目录（开发态）
        Path.cwd() / "router",
        desktop / "router",
    ]
    return out


def find_router_dir() -> Path | None:
    for p in _candidates():
        try:
            if (p / "hccfit" / "strategies.py").is_file():
                return p.resolve()
        except OSError:
            continue
    return None


def ensure_engine():
    """导入并返回 `hccfit.strategies`；失败时抛 RouterEngineError（中文原因）。

    打包态（PyInstaller）已经把 hccfit 及其 toolkit 冻进程序，此时找不到源码目录也能导入；
    开发态则把包内同级 `router/` 加进 sys.path 后再导入。
    """
    global _MODULE, _PATH
    if _MODULE is not None:
        return _MODULE
    path = find_router_dir()
    if path is not None and str(path) not in sys.path:
        sys.path.insert(0, str(path))
    try:
        import hccfit.strategies as strategies  # noqa: PLC0415  延迟导入，避免启动变慢
    except Exception as exc:  # noqa: BLE001
        hint = ""
        if path is None:
            hint = ("；开发态请保留包内 router/ 目录，或用 HCCFIT_ROUTER_DIR 指向它，"
                    "打包态请用 HCC-Fit.spec 重新构建")
        raise RouterEngineError("内置 Router 导入失败：%s: %s%s"
                                % (type(exc).__name__, exc, hint))
    _MODULE = strategies
    # 打包态导入的是 PYZ 里的模块；此时不要因为旁边恰好有 router/ 源码就误报“用的是源码”
    mod_file = str(getattr(strategies, "__file__", ""))
    meipass = getattr(sys, "_MEIPASS", "")
    _PATH = None if (meipass and mod_file.startswith(meipass)) else path
    return strategies


def reset_cache() -> None:
    """测试用：忘掉已导入的引擎。"""
    global _MODULE, _PATH
    _MODULE = None
    _PATH = None


def engine_status() -> dict:
    """给界面看的引擎状态；不联网、不抛异常。"""
    try:
        strategies = ensure_engine()  # noqa: F841
        from hccfit import config  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return {
            "available": False,
            "error": "%s: %s" % (type(exc).__name__, exc),
            "path": "",
            "strategies": [],
            "sciencepal_configured": False,
            "filter_configured": False,
        }
    return {
        "available": True,
        "error": "",
        "path": str(_PATH) if _PATH else "（打包内置）",
        "strategies": list(config.ENABLED_STRATEGIES),
        "sciencepal_configured": bool(config.SCIENCEPAL_API_KEY),
        "filter_configured": bool(config.SCIENCEPAL_FILTER_AGENT_ID),
        "sciencepal_base": config.SCIENCEPAL_BASE_URL,
        "sciencepal_model": config.SCIENCEPAL_MODEL,
    }


def _apply_runtime_config(sciencepal_key: str = "", filter_agent_id: str = "",
                          base_url: str = "") -> None:
    """把界面里填的值写进 Router 的 config（只在内存，不落盘、不改文件）。"""
    from hccfit import config  # noqa: PLC0415

    if filter_agent_id.strip():
        config.SCIENCEPAL_FILTER_AGENT_ID = filter_agent_id.strip()
    if base_url.strip():
        config.SCIENCEPAL_BASE_URL = base_url.strip().rstrip("/")
    if sciencepal_key.strip():
        config.SCIENCEPAL_API_KEY = sciencepal_key.strip()


def search(scenario: dict, strategy: str = "fast", k: int = 8,
           sciencepal_key: str = "", filter_agent_id: str = "",
           base_url: str = "", deadline_ms: int | None = None) -> dict:
    """进程内检索：参数/返回与 HTTP `/v1/search` 一致（`scenario` 见 router 的 schema）。"""
    strategies = ensure_engine()
    from hccfit.scope import ScopeError  # noqa: PLC0415

    _apply_runtime_config(sciencepal_key, filter_agent_id, base_url)
    if deadline_ms is None:
        deadline_ms = 100000 if strategy == "auto" else 150000
    try:
        return strategies.run(
            dict(scenario or {}), strategy=strategy, k=max(1, min(8, int(k))),
            deadline_ms=int(deadline_ms), thinking=False, require_fulltext=False,
            api_key=(sciencepal_key.strip() or None),
        )
    except ScopeError as exc:
        raise RouterEngineError("%s: %s" % (
            getattr(exc, "code", "out_of_scope"), getattr(exc, "message", str(exc))))
    except RouterEngineError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise RouterEngineError("%s: %s" % (type(exc).__name__, exc))
