# -*- coding: utf-8 -*-
"""数据目录：不可写时必须自动回退（解决 AppData\LocalLow 权限拒绝）。"""
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


def main():
    check("HCCFIT_DATA_DIR 可写", case_override_used)
    check("不可写目录自动回退", case_unwritable_falls_back)
    ok = sum(1 for r in RESULTS if r)
    print("\n%d/%d 通过" % (ok, len(RESULTS)))
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    sys.exit(main())
