# -*- coding: utf-8 -*-
"""
pdftext.py — 纯标准库的"够用版" PDF 取字。

为什么需要：平台报 `workspace_context_prepare_failed`（"Could not prepare the selected files"）
时，Agent 拿不到附件。这时把 PDF 里能抠出的文字直接塞进 prompt，至少让 Agent 有东西可读。

局限（要老实说）：只对**含文本层**的 PDF 有效；扫描件、CID 字体会抠出乱码，此时返回空串。
"""

from __future__ import annotations

import re
import zlib

_MIN_CHARS = 200
_MIN_RATIO = 0.55          # 可见 ASCII/中文占比低于此值就认为抠失败


def _looks_like_text(text):
    """真文本：几乎全是可打印 ASCII/中文，且字母比例高；否则多半是字体程序/乱码。"""
    if len(text) < _MIN_CHARS:
        return False
    printable = sum(1 for c in text if 32 <= ord(c) < 127 or "\u4e00" <= c <= "\u9fff")
    letters = sum(1 for c in text if (c.isalpha() and c.isascii()) or "\u4e00" <= c <= "\u9fff")
    if printable / len(text) < 0.90:
        return False
    if letters / len(text) < 0.45:
        return False
    return bool(__import__("re").search(r"\b(the|and|of|patients?|HCC|recurrence)\b", text, re.I))


def extract(path, limit=12000):
    """返回 (text, note)。text 为空表示抠不出来。"""
    try:
        data = open(path, "rb").read()
    except OSError as exc:
        return "", "读取失败：%s" % exc
    if not data.startswith(b"%PDF"):
        return "", "不是 PDF（或已加密）"

    chunks = []
    for raw in re.finditer(rb"stream\r?\n(.*?)\r?\nendstream", data, re.S):
        blob = raw.group(1)
        try:
            body = zlib.decompress(blob)
        except Exception:
            body = blob
        # (…)Tj 与 [(…)…]TJ
        for lit in re.findall(rb"\((?:\\.|[^\\()])*\)", body):
            s = lit[1:-1]
            s = s.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
            try:
                chunks.append(s.decode("latin-1"))
            except Exception:
                continue

    text = re.sub(r"[ \t]+", " ", " ".join(chunks))
    text = re.sub(r"\s*\n\s*", "\n", text).strip()
    if not _looks_like_text(text):
        return "", "没抠到可用文本（可能是扫描件/特殊字体），本次不带论文正文"
    return text[:limit], "已从 PDF 抠出 %d 字" % min(len(text), limit)
