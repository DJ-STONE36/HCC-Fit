# -*- coding: utf-8 -*-
"""SciencePal API Key 的前置校验（只查格式/字符，不联网）。

- 只做“能不能拿去发请求”的检查：空、非 ASCII、空格换行、明显过短、前缀可疑。
- 真正的有效性仍以平台为准：界面的“测试连接 / 看 Agent”会调 401 验证。
"""
from __future__ import annotations

import re

MIN_LEN = 16
_LIKELY_PREFIXES = ("sp_", "hccfit_")


def check_key(raw: str) -> tuple[str, str]:
    """返回 (level, message)，level ∈ ok / warn / error。"""
    key = (raw or "").strip()
    if not key:
        return "error", "未填 SciencePal API Key（SciencePal 模式与 balanced 检索需要）"
    bad = [c for c in key if ord(c) > 127]
    if bad:
        return "error", ("Key 含非 ASCII 字符（%s）：通常是复制时带进了中文标点/全角空格/零宽字符，请重新复制"
                         % "、".join(repr(c) for c in bad[:4]))
    if re.search(r"\s", key):
        return "error", "Key 含空格或换行，请重新复制"
    if len(key) < MIN_LEN:
        return "warn", "Key 只有 %d 位，明显偏短，请确认是否复制完整" % len(key)
    if not key.startswith(_LIKELY_PREFIXES):
        return "warn", "Key 不以 sp_ / hccfit_ 开头，确认复制的是 API Key（不是登录令牌）"
    return "ok", "Key 格式正常（%d 位）" % len(key)
