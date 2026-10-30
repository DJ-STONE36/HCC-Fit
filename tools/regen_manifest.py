# -*- coding: utf-8 -*-
"""重新生成 manifest.sha256（与 2026-10-30 交接包一致的格式：sha256 + 两空格 + 相对路径 + CRLF）。"""
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREFIXES = ("HANDOFF.md", "desktop", "desktop_build", "docs", "router", "tools")
SKIP_DIRS = {"__pycache__", "dist", "build", "smoke_out", "smoke_data", "eggs"}
SKIP_PREFIX = ("desktop/tests/_tmp",)


def collect():
    out = []
    for prefix in PREFIXES:
        abs_prefix = os.path.join(ROOT, prefix)
        if os.path.isfile(abs_prefix):
            out.append(prefix)
            continue
        for base, dirs, files in os.walk(abs_prefix):
            dirs[:] = [d for d in dirs
                       if d not in SKIP_DIRS and not d.startswith(".git")]
            rel_base = os.path.relpath(base, ROOT).replace(os.sep, "/")
            if any(rel_base.startswith(p) for p in SKIP_PREFIX):
                continue
            for name in files:
                if name.endswith((".pyc", ".pyo")):
                    continue
                rel = os.path.relpath(os.path.join(base, name), ROOT).replace(os.sep, "/")
                if rel.startswith(SKIP_PREFIX):
                    continue
                out.append(rel)
    return sorted(set(out), key=lambda p: (p.lower(), p))


def main():
    files = collect()
    lines = []
    for rel in files:
        with open(os.path.join(ROOT, rel), "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        lines.append("%s  %s" % (digest, rel))
    text = "\r\n".join(lines) + "\r\n"
    path = os.path.join(ROOT, "manifest.sha256")
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(text)
    print("wrote %d entries to %s" % (len(lines), path))

    # 自检：逐条重算
    bad = 0
    for line in text.split("\r\n"):
        if not line:
            continue
        digest, rel = line.split("  ", 1)
        with open(os.path.join(ROOT, rel), "rb") as fh:
            if hashlib.sha256(fh.read()).hexdigest() != digest:
                bad += 1
                print("MISMATCH", rel)
    print("verify: %s" % ("OK" if bad == 0 else "%d 处不一致" % bad))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
