# -*- coding: utf-8 -*-
"""方案 A：可选 PubMed 候选检索。

只提供**内置引擎**（进程内调用 hccfit），不需要启动外部服务、不填 Key/filter agent：
- SciencePal Key 由主窗口“1 · 大脑 / SciencePal 连接”统一提供；
- filter agent 由环境变量 `SCIENCEPAL_FILTER_AGENT_ID` 配置；
- 外部 HTTP 服务代码（router/ + RouterClient）保留，但不在界面暴露。
"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QThread, QTimer
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QTextBrowser, QVBoxLayout,
)

from app import paths
from app.keycheck import check_key
from app.links import open_external_url
from app.router_client import RouterWorker


class RouterDialog(QDialog):
    def __init__(self, scenario: dict, sciencepal_key: str = "", parent=None):
        super().__init__(parent)
        self.scenario = scenario
        self.sciencepal_key = (sciencepal_key or "").strip()
        self.thread = None
        self.worker = None
        self.candidate_path = None
        self.papers = []

        self.setWindowTitle("先找候选论文（PubMed）")
        self.resize(1040, 680)
        lay = QVBoxLayout(self)

        lay.addWidget(QLabel("检索方式：内置引擎（进程内，无需启动服务）"))

        self.engine_msg = QLabel("")
        self.engine_msg.setWordWrap(True)
        self.engine_msg.setStyleSheet("color:#66727f;font-size:9pt;")
        lay.addWidget(self.engine_msg)

        row = QHBoxLayout()
        self.strategy = QComboBox()
        self.strategy.addItem("fast（5–10 秒）", "fast")
        self.strategy.addItem("balanced（60–90 秒）", "balanced")
        self.strategy.currentIndexChanged.connect(self._refresh_status)
        self.k = QSpinBox()
        self.k.setRange(1, 8)
        self.k.setValue(8)
        self.search_btn = QPushButton("一键检索候选")
        self.search_btn.clicked.connect(self.search)
        row.addWidget(QLabel("策略"))
        row.addWidget(self.strategy)
        row.addWidget(QLabel("k"))
        row.addWidget(self.k)
        row.addStretch(1)
        row.addWidget(self.search_btn)
        lay.addLayout(row)

        self.status = QLabel("只做候选发现；题录/摘要不是全文，S2 四维核查仍需 PDF。")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#8a5a00;background:#fff5e0;border:1px solid #f0dcb0;"
                                  "border-radius:6px;padding:6px;")
        lay.addWidget(self.status)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["选", "标题", "年", "期刊", "可读性", "为什么 / 风险"])
        self.table.setWordWrap(True)
        self.table.setAlternatingRowColors(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)   # 标题不省略
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Interactive)                  # 行高由 _fit_rows 统一算
        h = self.table.horizontalHeader()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(0, 44)          # 选
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)   # 标题：占剩余宽度
        h.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(2, 64)          # 年
        h.setSectionResizeMode(3, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(3, 150)         # 期刊
        h.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(4, 96)          # 可读性
        h.setSectionResizeMode(5, QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(5, 240)         # 为什么/风险（比标题窄）
        self.table.itemDoubleClicked.connect(lambda _i: self.open_fulltext())
        self.table.horizontalHeader().sectionResized.connect(self._schedule_fit)
        lay.addWidget(self.table, 1)

        self.warn = QTextBrowser()
        self.warn.setFixedHeight(70)
        lay.addWidget(self.warn)

        bar = QHBoxLayout()
        self.full_btn = QPushButton("打开 PMC / PubMed")
        self.full_btn.clicked.connect(self.open_fulltext)
        self.doi_btn = QPushButton("复制 DOI")
        self.doi_btn.clicked.connect(self.copy_doi)
        self.url_btn = QPushButton("复制链接")
        self.url_btn.setToolTip("浏览器没弹出来时，复制链接手动粘贴")
        self.url_btn.clicked.connect(self.copy_url)
        self.add_btn = QPushButton("保存选中候选并加入论文列表")
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self.save_candidates)
        bar.addWidget(self.full_btn)
        bar.addWidget(self.doi_btn)
        bar.addWidget(self.url_btn)
        bar.addStretch(1)
        bar.addWidget(self.add_btn)
        lay.addLayout(bar)

        self._refresh_status()

    # ---------------- 状态 ----------------
    def current_strategy(self) -> str:
        return self.strategy.currentData() or "fast"

    def _refresh_status(self):
        from app.router_engine import engine_status
        st = engine_status()
        if not st["available"]:
            self.engine_msg.setText("内置引擎不可用：%s" % st.get("error"))
            self.search_btn.setEnabled(False)
            return
        self.search_btn.setEnabled(True)
        level, _msg = check_key(self.sciencepal_key)
        key_text = {"ok": "已配置", "warn": "疑似不完整", "error": "未配置/格式异常"}[level]
        if os.environ.get("SCIENCEPAL_API_KEY") and level == "error":
            key_text = "已由环境变量配置"
        filter_text = "已配置" if st.get("filter_configured") else "未配置（将自动选可用 Agent，失败则降级 fast）"
        self.engine_msg.setText(
            "内置引擎可用 · 策略 %s · SciencePal Key %s · filter agent %s"
            % ("/".join(st.get("strategies") or []), key_text, filter_text))

    # ---------------- 检索 ----------------
    def search(self):
        if self.thread is not None:
            return
        strategy = self.current_strategy()
        if strategy == "balanced":
            level, msg = check_key(self.sciencepal_key)
            if level == "error" and not os.environ.get("SCIENCEPAL_API_KEY"):
                QMessageBox.warning(self, "缺少 SciencePal Key",
                                    msg + "\n\nfast 不需要 Key；balanced 必须在“1 · 大脑”里填 Key。")
                return
        self.search_btn.setEnabled(False)
        self.status.setText("检索中…（内置引擎，无需服务）")
        self.thread = QThread(self)
        self.worker = RouterWorker(
            self.scenario, strategy, self.k.value(), self.sciencepal_key,
            engine="builtin",
        )
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.done.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._thread_done)
        self.thread.start()

    def _thread_done(self):
        if self.thread is not None:
            self.thread.deleteLater()
        self.thread = None
        self.worker = None

    def _on_done(self, data: dict):
        self.search_btn.setEnabled(True)
        self.papers = data.get("papers") or []
        self.table.setRowCount(0)
        for p in self.papers:
            row = self.table.rowCount()
            self.table.insertRow(row)
            check = QTableWidgetItem("")
            check.setFlags(Qt.ItemFlag.ItemIsUserCheckable | Qt.ItemFlag.ItemIsEnabled)
            check.setCheckState(Qt.CheckState.Checked)
            check.setData(Qt.ItemDataRole.UserRole, p)
            self.table.setItem(row, 0, check)
            title = QTableWidgetItem(str(p.get("title") or ""))
            title.setToolTip(str(p.get("title") or ""))
            self.table.setItem(row, 1, title)
            self.table.setItem(row, 2, QTableWidgetItem(str(p.get("year") or "")))
            self.table.setItem(row, 3, QTableWidgetItem(str(p.get("journal") or "")))
            rd = p.get("readability") or {}
            self.table.setItem(row, 4, QTableWidgetItem(str(rd.get("level") or "")))
            why = "；".join(p.get("why") or [])
            risk = "；".join(p.get("risk") or [])
            extra = "why: %s\nrisk: %s" % (why or "—", risk or "—")
            cell = QTableWidgetItem(extra)
            cell.setToolTip(extra)
            self.table.setItem(row, 5, cell)
        # 行高按内容自适应（布局完成后再算，否则会用“还没撑开的列宽”算出超高行）
        self._schedule_fit()
        warnings = data.get("warnings") or []
        degraded = (data.get("meta") or {}).get("degraded")
        self.warn.setPlainText("\n".join(warnings) or ("已降级：SciencePal 不可用" if degraded else "无警告"))
        used = data.get("strategy_used") or ""
        self.status.setText("返回 %d 篇，实际策略：%s；勾选后保存，再回论文区添加 PDF。"
                            % (len(self.papers), used or "—"))
        self.add_btn.setEnabled(bool(self.papers))
        if not self.papers:
            self.status.setText("没有找到候选；可改用 fast、放宽条件，或直接在论文区添加 PDF。")

    def _fit_rows(self):
        if not self.table.rowCount():
            return
        self.table.resizeRowsToContents()
        for row in range(self.table.rowCount()):
            if self.table.rowHeight(row) < 44:
                self.table.setRowHeight(row, 44)

    def _schedule_fit(self, *_args):
        QTimer.singleShot(0, self._fit_rows)

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_fit()

    def _on_failed(self, msg: str):
        self.search_btn.setEnabled(True)
        self.status.setText("检索失败：%s" % msg)
        self.warn.setPlainText(msg)

    # ---------------- 候选操作 ----------------
    def _paper_at(self, row: int):
        if row < 0 or row >= self.table.rowCount():
            return None
        cell = self.table.item(row, 0)
        return cell.data(Qt.ItemDataRole.UserRole) if cell else None

    def _row_paper(self):
        """当前行；没有选中时退回第一行（按钮不再“点了没反应”）。"""
        p = self._paper_at(self.table.currentRow())
        if p:
            return p
        for row in range(self.table.rowCount()):
            p = self._paper_at(row)
            if p:
                return p
        return None

    def _paper_url(self, p) -> str:
        rd = p.get("readability") or {}
        url = str(rd.get("url") or "").strip()
        pmid = str(p.get("pmid") or "").strip()
        if not url and pmid:
            url = "https://pubmed.ncbi.nlm.nih.gov/%s/" % pmid
        return url

    def open_fulltext(self):
        p = self._row_paper()
        if not p:
            QMessageBox.information(self, "没有候选", "请先“一键检索候选”，再打开论文链接。")
            return
        url = self._paper_url(p)
        if not url:
            QMessageBox.information(self, "没有链接", "这篇候选没有 PMID/链接，无法打开。")
            return
        ok, how = open_external_url(url)
        if ok:
            self.status.setText("已请求浏览器打开（%s）：%s　·　若没反应，点“复制链接”手动粘贴"
                                % (how, url))
            return
        QMessageBox.information(
            self, "未能自动打开浏览器",
            "系统拒绝了自动打开（%s）。\n链接已复制到剪贴板，可粘贴到浏览器：\n%s"
            % (how, url))

    def copy_url(self):
        p = self._row_paper()
        if not p:
            QMessageBox.information(self, "没有候选", "请先“一键检索候选”。")
            return
        url = self._paper_url(p)
        if not url:
            QMessageBox.information(self, "没有链接", "这篇候选没有 PMID/链接。")
            return
        QApplication.clipboard().setText(url)
        self.status.setText("链接已复制：%s" % url)

    def copy_doi(self):
        p = self._row_paper()
        if p and p.get("doi"):
            QApplication.clipboard().setText(str(p["doi"]))

    def save_candidates(self):
        selected = self._selected_papers()
        if not selected:
            if not self.papers:
                QMessageBox.warning(self, "没有候选", "请先“一键检索候选”。")
                return
            if QMessageBox.question(
                    self, "未勾选候选",
                    "没有勾选任何候选。要保存全部 %d 篇吗？" % len(self.papers)) \
                    != QMessageBox.StandardButton.Yes:
                return
            selected = list(self.papers)
        out_dir = paths.app_data_dir() / "router_candidates"
        path = out_dir / ("router_candidates_%s.md" % time.strftime("%Y%m%d_%H%M%S"))
        lines = [
            "# PubMed 候选论文（Router）",
            "",
            "> 这是题录/摘要，不是论文全文。S2 四维核查需要全文；",
            "> 请下载 PDF 后回到“论文”区添加，或把本文件仅作为候选清单。",
            "",
        ]
        for i, p in enumerate(selected, 1):
            rd = p.get("readability") or {}
            lines += [
                "## %d. %s" % (i, p.get("title") or ""),
                "- PMID: %s" % (p.get("pmid") or "—"),
                "- DOI: %s" % (p.get("doi") or "—"),
                "- 年/期刊: %s / %s" % (p.get("year") or "—", p.get("journal") or "—"),
                "- 可读性: %s" % (rd.get("level") or "—"),
                "- 链接: %s" % (rd.get("url") or "—"),
                "- why: %s" % "；".join(p.get("why") or []),
                "- risk: %s" % "；".join(p.get("risk") or []),
                "- 摘要: %s" % (p.get("abstract_snippet") or "—"),
                "",
            ]
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败",
                                 "%s: %s\n目标目录：%s" % (type(exc).__name__, exc, out_dir))
            return
        self.candidate_path = str(path)
        self.status.setText("已保存 %d 篇候选：%s" % (len(selected), path.name))
        self.accept()

    def _selected_papers(self):
        out = []
        for row in range(self.table.rowCount()):
            cell = self.table.item(row, 0)
            if cell and cell.checkState() == Qt.CheckState.Checked:
                p = cell.data(Qt.ItemDataRole.UserRole)
                if p:
                    out.append(p)
        return out

    def closeEvent(self, event):
        if self.thread is not None:
            QMessageBox.information(self, "正在检索", "检索还没结束，请稍等。")
            event.ignore()
            return
        event.accept()
