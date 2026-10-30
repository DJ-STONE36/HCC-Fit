# -*- coding: utf-8 -*-
"""单输入场景卡：解析 → 字段确认 → 集中追问 → 确认后交给 Agent v0.6。"""
from __future__ import annotations

import re
from html import escape

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QHeaderView,
    QLabel, QLineEdit, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
    QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

from app import input_store
from app.input_normalization import (
    FIELD_LABEL, FIELD_ORDER, STATUS_LABEL, InputSession,
    RuleExtractor, field_sufficient, sufficient_fields,
)

STATUS_COLOR = {
    "normalized": ("#1e8449", "#eafaf1"),
    "ambiguous": ("#8a5a00", "#fff4e0"),
    "suspected": ("#8a5a00", "#fff4e0"),
    "negated": ("#c0392b", "#fdedec"),
    "historical": ("#6c3483", "#f4ecf7"),
    "conflict": ("#c0392b", "#fdedec"),
    "unknown": ("#66727f", "#f4f6f8"),
    "out_of_scope": ("#c0392b", "#fdedec"),
}


class InputReviewDialog(QDialog):
    def __init__(self, raw_text: str, terms_path, parent=None):
        super().__init__(parent)
        self.raw_text = (raw_text or "").strip()
        self.terms_path = terms_path
        self.rules = RuleExtractor(terms_path)
        self.session = self.rules.extract(self.raw_text)
        self.row_fields: list[tuple[str, object]] = []
        self._loading = False
        self.follow_widgets = {}

        self.setWindowTitle("场景卡确认")
        self.resize(1080, 760)
        self._build_ui()
        self._populate_table()
        self._populate_followups()
        self._update_confirm()

    # ---------------- UI ----------------
    def _build_ui(self):
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel("医生原话（保存用，不会被覆盖）"))
        raw = QPlainTextEdit(self.raw_text)
        raw.setReadOnly(True)
        raw.setFixedHeight(84)
        lay.addWidget(raw)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["字段", "原话", "整理值（可改）", "状态", "来源片段", "确认", "信息"])
        self.table.setWordWrap(True)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(30)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(5, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(6, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._on_table_edit)
        lay.addWidget(self.table, 3)

        lay.addWidget(QLabel("集中追问（只问影响启动的关键项；可以选“不知道”）"))
        self.follow_scroll = QScrollArea()
        self.follow_scroll.setWidgetResizable(True)
        self.follow_body = QWidget()
        self.follow_layout = QVBoxLayout(self.follow_body)
        self.follow_scroll.setWidget(self.follow_body)
        self.follow_scroll.setFixedHeight(190)
        lay.addWidget(self.follow_scroll)

        self.hint = QLabel("")
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet("color:#66727f;")
        lay.addWidget(self.hint)

        bar = QHBoxLayout()
        self.draft_btn = QPushButton("保存草稿")
        self.draft_btn.clicked.connect(self._save_draft)
        self.reparse_btn = QPushButton("重新解析")
        self.reparse_btn.clicked.connect(self._reparse)
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.clicked.connect(self.reject)
        self.ok_btn = QPushButton("确认场景卡，进入核查")
        self.ok_btn.setMinimumHeight(38)
        self.ok_btn.setStyleSheet(
            "QPushButton { background:#0f1722;color:white;font-weight:600;border-radius:8px; }"
            "QPushButton:disabled { background:#d9e0e8;color:#66727f; }")
        self.ok_btn.clicked.connect(self._confirm)
        bar.addWidget(self.draft_btn)
        bar.addWidget(self.reparse_btn)
        bar.addStretch(1)
        bar.addWidget(self.cancel_btn)
        bar.addWidget(self.ok_btn)
        lay.addLayout(bar)

    # ---------------- 表格 ----------------
    def _iter_rows(self):
        for name in FIELD_ORDER:
            f = self.session.fields.get(name)
            if f:
                yield ("field", name, f)
        for i, d in enumerate(self.session.data_available):
            yield ("data", i, d)

    def _populate_table(self):
        self._loading = True
        self.table.setRowCount(0)
        self.row_fields = []
        for kind, key, f in self._iter_rows():
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.row_fields.append((kind, key))
            enough = kind == "field" and field_sufficient(self.session, key)
            vals = [
                FIELD_LABEL.get(f.field, f.field) if kind == "field" else "资料",
                f.raw or "—",
                f.normalized or "—",
                STATUS_LABEL.get(f.status, f.status),
                "[%d,%d]" % tuple(f.source_span) if f.source_span else "—",
                f.confirm,
                ("足够" if enough else "—") if kind == "field" else "—",
            ]
            for col, text in enumerate(vals):
                item = QTableWidgetItem(str(text))
                if col != 2 or kind == "data":
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                if col == 0:
                    item.setFont(QFont(self.font().family(), 9, QFont.Weight.Bold))
                if col == 3:
                    fg, bg = STATUS_COLOR.get(f.status, ("#333", "#f4f6f8"))
                    item.setForeground(QBrush(QColor(fg)))
                    item.setBackground(QBrush(QColor(bg)))
                if col == 6 and enough:
                    item.setForeground(QBrush(QColor("#1e8449")))
                    item.setToolTip("原话已提供足够信息，不再追问")
                if col == 1 and f.source_sentence:
                    item.setToolTip(f.source_sentence)
                if col == 4 and f.source_sentence:
                    item.setToolTip(f.source_sentence)
                self.table.setItem(row, col, item)
            self.table.setRowHeight(row, 30)
        self.table.resizeRowsToContents()
        self._loading = False

    def _on_table_edit(self, item):
        if self._loading or item.column() != 2:
            return
        idx = item.row()
        if idx < 0 or idx >= len(self.row_fields):
            return
        kind, key = self.row_fields[idx]
        if kind != "field":
            return
        f = self.session.fields[key]
        text = item.text().strip()
        if not text:
            return
        f.normalized = text
        f.status = "normalized"
        f.confirm = "user_edited"
        f.concept_id = ""          # 医生改写后不再对应术语表条目，按自由文本记录
        self.session.conflicts = [c for c in self.session.conflicts if c.field != key]
        # 医生直接改了字段，就把对应追问视为已回答；冲突清空后关闭 q_conflict
        for fu in self.session.followups:
            if fu.field == key and fu.answer_status == "pending":
                fu.answer = "（已在场景卡修改）"
                fu.answer_status = "answered"
            if fu.field == "conflict" and not self.session.conflicts:
                fu.answer = "（已在场景卡修改）"
                fu.answer_status = "answered"
        # 更新状态/确认列，不重建表格，避免打断编辑
        self._loading = True
        self.table.item(idx, 3).setText(STATUS_LABEL["normalized"])
        self.table.item(idx, 3).setForeground(QBrush(QColor("#1e8449")))
        self.table.item(idx, 3).setBackground(QBrush(QColor("#eafaf1")))
        self.table.item(idx, 5).setText("user_edited")
        enough = field_sufficient(self.session, key)
        self.table.item(idx, 6).setText("足够" if enough else "—")
        self.table.item(idx, 6).setForeground(
            QBrush(QColor("#1e8449" if enough else "#333")))
        self._loading = False
        self._update_confirm()

    # ---------------- 追问 ----------------
    def _clear_followups(self):
        while self.follow_layout.count():
            item = self.follow_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

    def _populate_followups(self):
        self._clear_followups()
        self.follow_widgets = {}
        if not self.session.followups:
            self.follow_layout.addWidget(QLabel("没有必答追问。确认场景卡即可。"))
            return
        for f in self.session.followups:
            box = QWidget()
            v = QVBoxLayout(box)
            v.setContentsMargins(0, 2, 0, 6)
            q = QLabel(("• " if f.required else "◦ ") + f.question)
            q.setWordWrap(True)
            q.setStyleSheet("font-weight:600;" if f.required else "color:#66727f;")
            v.addWidget(q)
            combo = QComboBox()
            combo.addItem("请选择…", "")
            for opt in f.options:
                combo.addItem(opt, opt)
            if f.answer:
                at = combo.findData(f.answer)
                if at >= 0:
                    combo.setCurrentIndex(at)
            combo.currentIndexChanged.connect(lambda _i, f=f, c=combo: self._on_answer(f, c))
            v.addWidget(combo)
            edit = None
            if "自定义输入" in f.options:
                edit = QLineEdit()
                edit.setPlaceholderText("输入你的问题")
                edit.textChanged.connect(lambda _t, f=f, e=edit: self._on_custom_answer(f, e))
                v.addWidget(edit)
            self.follow_widgets[f.id] = (combo, edit)
            self.follow_layout.addWidget(box)
        self.follow_layout.addStretch(1)

    def _on_custom_answer(self, f, edit):
        text = edit.text().strip()
        if not text:
            return
        f.answer = text
        f.answer_status = "answered"
        self._apply_answer(f, text)

    def _on_answer(self, f, combo):
        answer = combo.currentData()
        if not answer:
            return
        f.answer = answer
        f.answer_status = "unknown" if answer == "不知道" else "answered"
        self._apply_answer(f, answer)
        self._populate_table()
        self._update_confirm()

    def _apply_answer(self, f, answer):
        if answer == "自定义输入":        # 等医生在下面输入框里填具体内容
            return
        if f.field == "conflict":
            return
        target = self.session.fields.get(f.field)
        if target is None:
            return
        if answer == "不知道":
            target.status = "unknown"
            target.confirm = "user_unknown"
            target.flags.append("医生选择不知道")
            self._update_confirm()
            return

        if f.field == "disease":
            mapping = {
                "HCC": ("HCC", "normalized"),
                "本次 HCC": ("HCC", "normalized"),
                "已确诊 HCC": ("HCC", "normalized"),
                "其他类型": ("其他肝脏肿瘤，类型待明确", "out_of_scope"),
                "其他肝脏肿瘤": ("其他肝脏肿瘤，类型待明确", "out_of_scope"),
                "尚未明确": ("肝脏恶性肿瘤，类型未明确", "ambiguous"),
                "仍疑似": ("HCC 疑似", "suspected"),
                "已排除 HCC": ("HCC 被否定", "negated"),
                "本次非 HCC": ("HCC 被否定", "negated"),
                "既往 HCC，本次未定": ("HCC 既往史；当前病灶未定", "historical"),
                "以阳性为准": ("HCC", "normalized"),
                "以否定为准": ("HCC 被否定", "negated"),
                "返回修改": ("首版范围外", "out_of_scope"),
                "仅查看范围说明": ("首版范围外", "out_of_scope"),
            }
            if answer in mapping:
                target.normalized, target.status = mapping[answer]
        elif f.field == "treatment":
            mapping = {
                "已切除（根治性）": ("已接受根治性切除", "normalized"),
                "已切除（是否根治不明）": ("已接受切除，根治性未明确", "ambiguous"),
                "TACE/消融/系统治疗": ("TACE/消融/系统治疗（类型待明确）", "ambiguous"),
                "尚未治疗": ("未治疗", "normalized"),
            }
            if answer in mapping:
                target.normalized, target.status = mapping[answer]
        elif f.field == "start_point":
            mapping = {"手术日": ("手术日", "normalized"),
                       "治疗日": ("治疗日", "normalized"),
                       "今天": ("今天", "normalized")}
            if answer in mapping:
                target.normalized, target.status = mapping[answer]
        elif f.field == "horizon":
            mapping = {"1 年": "12个月", "3 年": "36个月", "5 年": "60个月"}
            if answer in mapping:
                target.normalized, target.status = mapping[answer], "normalized"
            else:
                m = re.match(r"(\d+)\s*(年|个月|月)", str(answer))
                if m:
                    n = int(m.group(1))
                    target.normalized = ("%d个月" % (n * 12)) if m.group(2) == "年" else ("%d个月" % n)
                    target.status = "normalized"
        elif f.field == "outcome":
            mapping = {"总生存 OS": ("总生存OS", "normalized"),
                       "复发 RFS": ("复发", "normalized"),
                       "其他": ("其他结局", "ambiguous")}
            if answer in mapping:
                target.normalized, target.status = mapping[answer]
        elif f.field == "intent":
            target.normalized, target.status = answer, "normalized"
        elif f.field == "subject":
            target.normalized = "患者本人" if answer == "患者本人" else "家属代问"
            target.status = "normalized" if answer == "患者本人" else "ambiguous"
        target.confirm = "user_edited"
        self.session.conflicts = [c for c in self.session.conflicts if c.field != f.field]

    # ---------------- 操作 ----------------
    def _update_confirm(self):
        pending = self.session.pending_required()
        conflicts = [c for c in self.session.conflicts]
        scope = self.session.fields.get("disease") and self.session.fields["disease"].status == "out_of_scope"
        if conflicts:
            self.hint.setText("存在未解决的冲突：%s；请在场景卡里修改后确认。" %
                              "、".join(sorted({c.field for c in conflicts})))
            self.ok_btn.setEnabled(False)
        elif scope:
            self.hint.setText("该诊断超出首版 HCC 范围；请修改疾病字段，或仅查看范围说明。")
            self.ok_btn.setEnabled(False)
        elif pending:
            self.hint.setText("还有 %d 个必答问题。" % len(pending))
            self.ok_btn.setEnabled(False)
        else:
            suf = sufficient_fields(self.session)
            key_fields = ("disease", "treatment", "outcome", "start_point", "intent")
            if all(suf.get(k) for k in key_fields):
                self.hint.setText("原话信息已足够（疾病/治疗/结局/起点/问题），不再追问；"
                                  "确认后会冻结为 input_session 并进入核查。")
            else:
                self.hint.setText("字段已整理完成；确认后会冻结为 input_session 并进入核查。")
            self.ok_btn.setEnabled(True)

    def _reparse(self):
        if QMessageBox.question(self, "重新解析", "会丢弃当前修改，重新从原话解析，继续？") != QMessageBox.StandardButton.Yes:
            return
        self.session = self.rules.extract(self.raw_text)
        self._populate_table()
        self._populate_followups()
        self._update_confirm()

    def _save_draft(self):
        try:
            p = input_store.save(self.session)
            QMessageBox.information(self, "已保存", "草稿：%s" % p)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.warning(self, "保存失败", "%s: %s" % (type(exc).__name__, exc))

    def _confirm(self):
        if not self.ok_btn.isEnabled():
            return
        if QMessageBox.question(self, "确认场景卡", "确认后字段将冻结，修改需重新确认（版本 +1）。继续？") != QMessageBox.StandardButton.Yes:
            return
        self.session.confirm()
        self.accept()

    @property
    def confirmed_session(self) -> InputSession:
        return self.session
