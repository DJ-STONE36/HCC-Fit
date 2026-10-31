# -*- coding: utf-8 -*-
r"""数据目录：不可写时必须自动回退（解决 AppData\LocalLow 权限拒绝）。"""
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app import paths  # noqa: E402

RESULTS = []


def check(name, fn):
    try:
        detail = fn()
        RESULTS.append(True)
        print("PASS  %-40s %s" % (name, detail or ""))
    except Exception as exc:  # noqa: BLE001
        RESULTS.append(False)
        print("FAIL  %-40s %s: %s" % (name, type(exc).__name__, exc))


def case_override_used():
    target = BASE / "tests" / "_tmp_paths_ok"
    os.environ["HCCFIT_DATA_DIR"] = str(target)
    try:
        got = paths.app_data_dir()
        assert got == target, (got, target)
        assert (target / ".hccfit_write_test").exists() is False
        assert got.is_dir()
    finally:
        os.environ.pop("HCCFIT_DATA_DIR", None)
        import shutil
        shutil.rmtree(target, ignore_errors=True)
    return "可写时用指定目录"


def case_unwritable_falls_back():
    bad = Path("C:/Windows/hccfit_should_not_write_here")
    os.environ["HCCFIT_DATA_DIR"] = str(bad)
    try:
        got = paths.app_data_dir()
        assert got != bad, got
        probe = got / ".probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    finally:
        os.environ.pop("HCCFIT_DATA_DIR", None)
    return "不可写时回退到：%s" % got


def case_open_directory():
    """打开工作区：空路径/不存在要明确报原因；成功走 startfile，失败退回 explorer。"""
    from app import links
    ok, why = links.open_directory("")
    assert not ok and "没有工作区路径" in why, why
    ok, why = links.open_directory(str(BASE / "no_such_workspace_dir"))
    assert not ok and "目录不存在" in why, why

    had = hasattr(os, "startfile")
    orig_start = getattr(os, "startfile", None)
    called = []
    os.startfile = lambda p: called.append(p)          # type: ignore[attr-defined]
    try:
        ok, how = links.open_directory(str(BASE))
        assert ok and how == "os.startfile", (ok, how)
        assert called == [str(BASE)], called

        # startfile 失败 → 退回 explorer.exe（同样只记录，不真开窗口）
        def boom(_p):
            raise OSError("no association")
        os.startfile = boom                             # type: ignore[attr-defined]
        import app.links as L
        orig_popen = L.subprocess.Popen
        launched = []
        L.subprocess.Popen = lambda args, **k: launched.append(list(args))
        try:
            ok, how = links.open_directory(str(BASE))
            assert ok and how == "explorer.exe", (ok, how)
            assert launched and launched[0][0] == "explorer.exe", launched
        finally:
            L.subprocess.Popen = orig_popen
    finally:
        if had:
            os.startfile = orig_start                   # type: ignore[attr-defined]
        else:
            delattr(os, "startfile")
    return "空/不存在有原因；startfile → explorer 回退"


def main():
    check("HCCFIT_DATA_DIR 可写", case_override_used)
    check("不可写目录自动回退", case_unwritable_falls_back)
    check("打开工作区回退链", case_open_directory)
    ok = sum(1 for r in RESULTS if r)
    print("\n%d/%d 通过" % (ok, len(RESULTS)))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
