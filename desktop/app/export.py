# -*- coding: utf-8 -*-
"""导出：JSON 给 Agent，PDF 给人看。"""
from __future__ import annotations

import json
import os
import re
from html import escape
from pathlib import Path

from PySide6.QtCore import QMarginsF
from PySide6.QtGui import (QFont, QFontDatabase, QPageLayout, QPageSize, QPdfWriter,
                           QTextDocument, QTextDocumentFragment)

STATUS = {
    "match": ("匹配", "#1e8449", "#eafaf1"),
    "mismatch": ("不匹配", "#c0392b", "#fdedec"),
    "unknown": ("无法确认", "#b9770e", "#fef5e7"),
}
TIER = {
    "applicable": "可直接讨论",
    "conditional": "有条件可用",
    "undetermined": "需补数据 / 无法确认",
    "not_applicable": "不适用",
}
STRENGTH = {"strong": "强", "moderate": "中", "weak": "弱", "insufficient": "不足"}
MODE_LABEL = {
    "stub": "离线自检（非真实结论）",
    "sciencepal": "SciencePal Agent",
    "sciencepal_hcc": "SciencePal · HCC-Fit 单次报告",
}


def export_json(visit: dict, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(visit, fh, ensure_ascii=False, indent=2)


def _status_cell(status):
    label, fg, bg = STATUS.get(status, (status or "未知", "#333", "#f5f5f5"))
    return '<span style="color:%s;background:%s;padding:1px 6px;border-radius:6px;">%s</span>' % (
        fg, bg, escape(label))


def cjk_font_family() -> str:
    """优先用系统 CJK 字体；找不到就显式加载 Windows 字体文件。"""
    families = set(QFontDatabase.families())
    for name in ("Microsoft YaHei", "Microsoft YaHei UI", "PingFang SC",
                 "Noto Sans CJK SC", "SimHei", "SimSun"):
        if name in families:
            return name
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            fid = QFontDatabase.addApplicationFont(path)
            if fid >= 0:
                got = QFontDatabase.applicationFontFamilies(fid)
                if got:
                    return got[0]
    return "Sans Serif"


def _md_html(text: str) -> str:
    """把 Agent 的 Markdown 结论转成 HTML 片段（PDF 里给人看）。

    QTextDocumentFragment.toHtml() 会返回完整 <html> 文档，直接嵌进外层
    会把整份报告冲掉；这里只取 <body> 里面的片段。
    """
    try:
        html = QTextDocumentFragment.fromMarkdown(text).toHtml()
        html = html.replace("<!--StartFragment-->", "").replace("<!--EndFragment-->", "")
        m = re.search(r"<body[^>]*>(.*?)</body>", html, flags=re.S | re.I)
        return m.group(1) if m else html
    except Exception:
        return "<div style='white-space:pre-wrap'>%s</div>" % escape(text)


def build_report_html(visit: dict, font_family: str = "Microsoft YaHei") -> str:
    scenario = visit.get("scenario") or {}
    papers = {p.get("paper_id"): p for p in (visit.get("papers") or [])}
    mode = visit.get("mode") or ""
    parts = []
    parts.append("""<html><head><style>
        body { font-family: '%s', 'Microsoft YaHei', 'PingFang SC', sans-serif; font-size: 10pt; color: #1f2933; }
        h1 { font-size: 18pt; margin: 0 0 2px 0; }
        .sub { color: #66727f; font-size: 9pt; }
        .warn { background: #fff4e0; border: 1px solid #f0dcb0; padding: 6px 8px; color: #8a5a00; }
        h2 { font-size: 12pt; margin: 14px 0 6px 0; border-bottom: 1px solid #d9e0e8; padding-bottom: 3px; }
        table { border-collapse: collapse; width: 100%; }
        th, td { border: 1px solid #d9e0e8; padding: 5px 7px; vertical-align: top; }
        th { background: #f2f5f9; text-align: left; }
        .muted { color: #66727f; }
        .quote { color: #4a5a72; font-style: italic; }
        .foot { color: #8a97a6; font-size: 8pt; margin-top: 14px; }
    </style></head><body>""".replace("%s", escape(font_family)))
    parts.append("<h1>HCC-Fit 核查报告</h1>")
    parts.append('<div class="sub">%s · %s · visit_id：%s</div>' % (
        escape(MODE_LABEL.get(mode, mode)), escape(str(visit.get("created_at") or "")),
        escape(str(visit.get("visit_id") or ""))))
    if mode == "stub":
        parts.append('<p class="warn"><b>演示数据，不是真实论文结论。</b></p>')
    if visit.get("parse_mode") == "text_fallback":
        parts.append('<p class="warn"><b>Agent 未按 JSON 返回</b>，以下为原文摘要；结论需人工判读。</p>')

    parts.append("<h2>1. 场景</h2><table>")
    rows = [
        ("目标人群", scenario.get("patient_summary_zh")),
        ("要回答的问题", scenario.get("task_zh")),
        ("使用时点", scenario.get("use_timepoint")),
        ("关心的结局", scenario.get("outcome_type")),
        ("已有资料", "、".join(scenario.get("inputs_given") or [])),
        ("缺失资料", "、".join(scenario.get("inputs_missing") or [])),
    ]
    for k, v in rows:
        parts.append("<tr><th style='width:120px'>%s</th><td>%s</td></tr>" % (
            escape(k), escape(str(v or "未填写"))))
    parts.append("</table>")

    domain = visit.get("domain") or {}
    dlabel, dfg, dbg = {
        "in_scope": ("域内（肝癌相关）", "#1e8449", "#eafaf1"),
        "outside_scope": ("域外", "#c0392b", "#fdedec"),
        "unclear": ("无法确定", "#b9770e", "#fef5e7"),
    }.get(domain.get("status"), ("未知", "#333", "#f5f5f5"))
    parts.append("<h2>2. 判定</h2><p><b style='color:%s'>%s</b> — %s</p>" % (
        dfg, escape(dlabel), escape(str(domain.get("note_zh") or ""))))

    parts.append("<h2>3. 综合结论</h2>")
    parts.append(_md_html(str((visit.get("integrated") or {}).get("summary_zh") or "")))

    parts.append("<h2>4. 四维核查</h2>")
    matched = visit.get("matched") or []
    if not matched:
        parts.append("<p class='muted'>没有结构化匹配结果（可能是纯文本降级）。</p>")
    for m in matched:
        p = papers.get(m.get("paper_id")) or {}
        tier = TIER.get(m.get("tier"), m.get("tier") or "")
        strength = STRENGTH.get(m.get("evidence_strength"), m.get("evidence_strength") or "—")
        parts.append("<p><b>%s</b>　档位：<b>%s</b>　证据强度：%s</p>" % (
            escape(str(p.get("title") or m.get("paper_id"))), escape(str(tier)), escape(str(strength))))
        parts.append("<table><tr><th style='width:70px'>维度</th><th style='width:90px'>结论</th>"
                     "<th>理由 / 原文引文</th></tr>")
        for d in m.get("dimensions") or []:
            dim_label = {"population": "人群", "timepoint": "时点",
                         "input": "输入", "outcome": "结局"}.get(d.get("id"), d.get("id"))
            reason = escape(str(d.get("reason_zh") or ""))
            quote = d.get("quote")
            loc = d.get("location")
            if quote:
                reason += "<br><span class='quote'>“%s”</span> <span class='muted'>（%s）</span>" % (
                    escape(str(quote)), escape(str(loc or "未标注")))
            parts.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                escape(str(dim_label)), _status_cell(d.get("status")), reason or "—"))
        parts.append("</table>")

    options = (visit.get("integrated") or {}).get("options") or []
    if options:
        parts.append("<h2>5. 方案（不构成治疗建议）</h2><table><tr><th>方案</th><th>说明</th></tr>")
        for o in options:
            parts.append("<tr><td>%s</td><td>%s</td></tr>" % (
                escape(str(o.get("name") or "")), escape(str(o.get("note_zh") or ""))))
        parts.append("</table>")

    expected = visit.get("expected_data") or []
    if expected:
        parts.append("<h2>6. 还缺什么数据</h2><table><tr><th>要补的</th><th>为什么</th><th>补齐可解锁</th></tr>")
        for e in expected:
            parts.append("<tr><td>%s</td><td>%s</td><td>%s</td></tr>" % (
                escape(str(e.get("item_zh") or "")), escape(str(e.get("why_zh") or "")),
                escape(str(e.get("unlocks_zh") or ""))))
        parts.append("</table>")

    uncertain = visit.get("uncertainties") or []
    if uncertain:
        parts.append("<h2>7. 我们不确定的事</h2><ul>")
        for u in uncertain:
            parts.append("<li>%s</li>" % escape(str(u)))
        parts.append("</ul>")

    cautions = (visit.get("integrated") or {}).get("cautions_zh") or []
    if cautions:
        parts.append("<h2>8. 注意点</h2><ul>")
        for c in cautions:
            parts.append("<li>%s</li>" % escape(str(c)))
        parts.append("</ul>")

    parts.append("<p class='foot'>%s</p>" % escape(str(visit.get("disclaimer_zh") or "")))
    parts.append("<p class='foot'>HCC-Fit · 仅用于临床决策前的证据准备 · 不作为诊断或治疗依据</p>")
    parts.append("</body></html>")
    return "".join(parts)


def export_pdf(visit: dict, path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = QPdfWriter(str(path))
    writer.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    writer.setPageMargins(QMarginsF(15, 15, 15, 15), QPageLayout.Unit.Millimeter)
    writer.setTitle("HCC-Fit 核查报告")
    family = cjk_font_family()
    doc = QTextDocument()
    doc.setDefaultFont(QFont(family, 9))
    doc.setHtml(build_report_html(visit, family))
    doc.print_(writer)
