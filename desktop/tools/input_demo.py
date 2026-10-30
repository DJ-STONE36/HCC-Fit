# -*- coding: utf-8 -*-
"""命令行演示：一段话 → 场景卡 + 追问 + 映射后的 scenario。"""
import json
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from app.input_normalization import RuleExtractor  # noqa: E402

TEXT = " ".join(sys.argv[1:]).strip() or "肝癌患者，刚切完，病理还没出来，想看看三年生存。"

rules = RuleExtractor(BASE / "resources" / "terms.csv")
s = rules.extract(TEXT)

print("原话：", s.raw_text)
print("\n字段：")
for name, f in s.fields.items():
    if f.status == "unknown" and not f.raw:
        continue
    print("  %-10s %-8s %-28s 原话=%r 来源=%s" % (
        name, f.status, f.normalized, f.raw, f.source_span))

print("\n已有资料：")
for d in s.data_available:
    print("  %-10s %-8s %s" % (d.status, d.normalized, d.raw))

print("\n冲突：")
for c in s.conflicts:
    print("  -", c.field, c.message)
if not s.conflicts:
    print("  （无）")

print("\n追问：")
for q in s.followups:
    print("  [%s] %s" % ("必答" if q.required else "可选", q.question))
if not s.followups:
    print("  （无）")

print("\n映射到 Agent v0.6 的 scenario：")
print(json.dumps(RuleExtractor.to_agent_scenario(s), ensure_ascii=False, indent=2))
