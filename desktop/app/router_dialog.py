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
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QHBoxLayout,
    QHeaderView, QLabel,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
    QTextBrowser, QVBoxLayout,
)

from app import paths
from app.keycheck import check_key
from app.links import open_external_url
from app.router_client import RouterWorker


READABILITY_ZH = {
    "fulltext": ("免费全文（PMC）", "PMC 收录，可直接阅读/下载全文"),
    "abstract_only": ("仅摘要（全文可能收费）", "PubMed 只提供摘要；全文通常需机构订阅或联系作者"),
    "metadata_only": ("仅题录（无摘要）", "没有取到摘要；需要看原文才能判断"),
}


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
        self.fulltext_only = QCheckBox("只看 PMC 免费全文")
        self.fulltext_only.setToolTip("勾选后过滤掉“仅摘要/仅题录”的候选；会多取一些再筛，返回可能少于 k 篇。")
        self.search_btn = QPushButton("一键检索候选")
        self.search_btn.clicked.connect(self.search)
        row.addWidget(QLabel("策略"))
        row.addWidget(self.strategy)
        row.addWidget(QLabel("k"))
        row.addWidget(self.k)
        row.addWidget(self.fulltext_only)
        row.addStretch(1)
        row.addWidget(self.search_btn)
        lay.addLayout(row)

        self.status = QLabel("只做候选发现；题录/摘要不是全文，S2 四维核查仍需 PDF。")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#8a5a00;background:#fff5e0;border:1px solid #f0dcb0;"
                                  "border-radius:6px;padding:6px;")
        lay.addWidget(self.status)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["选", "标题", "年", "期刊", "全文/摘要", "为什么 / 风险"])
        self.table.setWordWrap(True)
        self.table.setAlternatingRowColors(True)
        self.table.setTextElideMode(Qt.TextElideMode.ElideNone)   # 标题不省略
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
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
        self.table.itemDoubleClicked.connect(
            lambda _i: self.open_fulltext([self._paper_at(self.table.currentRow())]))
        self.table.horizontalHeader().sectionResized.connect(self._schedule_fit)
        lay.addWidget(self.table, 1)

        self.warn = QTextBrowser()
        self.warn.setFixedHeight(70)
        lay.addWidget(self.warn)

        bar = QHBoxLayout()
        self.full_btn = QPushButton("打开 PMC / PubMed")
        self.full_btn.setToolTip("打开选中（或勾选）的论文；多选会同时打开多个网页")
        # 注意：clicked 会带 checked(bool)；必须用 lambda 吞掉，否则会被当成 papers=False
        self.full_btn.clicked.connect(lambda: self.open_fulltext())
        self.doi_btn = QPushButton("复制 DOI")
        self.doi_btn.setToolTip("复制选中（或勾选）论文的 DOI；多选时每行一个")
        self.doi_btn.clicked.connect(lambda: self.copy_doi())
        self.url_btn = QPushButton("复制链接")
        self.url_btn.setToolTip("浏览器没弹出来时，复制链接手动粘贴")
        self.url_btn.clicked.connect(lambda: self.copy_url())
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
            engine="builtin", require_fulltext=self.fulltext_only.isChecked(),
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
            level = str(rd.get("level") or "")
            label, tip = READABILITY_ZH.get(level, (level or "—", ""))
            rcell = QTableWidgetItem(label)
            if tip:
                rcell.setToolTip(tip + chr(10) + str(rd.get("url") or ""))
            if level == "fulltext":
                rcell.setForeground(QBrush(QColor("#1e8449")))
            elif level == "abstract_only":
                rcell.setForeground(QBrush(QColor("#b9770e")))
            self.table.setItem(row, 4, rcell)
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
        counts = (data.get("meta") or {}).get("readability") or {}
        count_text = ""
        if counts:
            count_text = "（免费全文 %d / 仅摘要 %d / 仅题录 %d）" % (
                counts.get("fulltext", 0), counts.get("abstract_only", 0),
                counts.get("metadata_only", 0))
        self.status.setText("返回 %d 篇%s，实际策略：%s；勾选后保存，再回论文区添加 PDF。"
                            % (len(self.papers), count_text, used or "—"))
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

    def _target_papers(self):
        """要操作哪些论文：多选行优先；其次勾选；再次当前行/第一行。"""
        rows = sorted({idx.row() for idx in self.table.selectedIndexes()})
        papers = [self._paper_at(r) for r in rows]
        papers = [p for p in papers if p]
        if papers:
            return papers
        checked = self._selected_papers()
        if checked:
            return checked
        p = self._paper_at(self.table.currentRow()) or self._row_paper()
        return [p] if p else []

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

    def open_fulltext(self, papers=None):
        targets = papers if papers is not None else self._target_papers()
        if not targets:
            QMessageBox.information(self, "没有候选", "请先“一键检索候选”，再打开论文链接。")
            return
        urls = []
        for p in targets:
            u = self._paper_url(p)
            if u and u not in urls:
                urls.append(u)
        if not urls:
            QMessageBox.information(self, "没有链接", "选中的候选没有 PMID/链接，无法打开。")
            return
        if len(urls) > 3:
            if QMessageBox.question(
                    self, "打开多个链接",
                    "选中了 %d 篇，将同时打开 %d 个网页。继续？" % (len(targets), len(urls))) \
                    != QMessageBox.StandardButton.Yes:
                return
        ok_urls, failed = [], []
        for u in urls:
            ok, how = open_external_url(u)
            if ok:
                ok_urls.append(u)
            else:
                failed.append((u, how))
        if not failed:
            self.status.setText(
                "已请求浏览器打开 %d 个链接（%s）　·　若没反应，点“复制链接”手动粘贴"
                % (len(ok_urls), "多篇" if len(ok_urls) > 1 else "单篇"))
            return
        fail_text = chr(10).join(u for u, _ in failed)
        reason = failed[0][1] if failed else ""
        QApplication.clipboard().setText(fail_text)
        QMessageBox.information(
            self, "部分链接未能自动打开",
            "成功 %d 个，失败 %d 个。%s失败原因：%s%s失败链接已复制到剪贴板，可粘贴到浏览器：%s%s"
            % (len(ok_urls), len(failed), chr(10), reason, chr(10), chr(10), fail_text))

    def copy_url(self):
        targets = self._target_papers()
        if not targets:
            QMessageBox.information(self, "没有候选", "请先“一键检索候选”。")
            return
        urls = []
        for p in targets:
            u = self._paper_url(p)
            if u and u not in urls:
                urls.append(u)
        if not urls:
            QMessageBox.information(self, "没有链接", "选中的候选没有 PMID/链接。")
            return
        QApplication.clipboard().setText(chr(10).join(urls))
        self.status.setText("已复制 %d 个链接：%s" % (len(urls), "；".join(urls[:3])))

    def copy_doi(self):
        targets = self._target_papers()
        dois = [str(p.get("doi")).strip() for p in targets if p.get("doi")]
        if not dois:
            QMessageBox.information(self, "没有 DOI", "选中的候选没有 DOI。")
            return
        QApplication.clipboard().setText(chr(10).join(dois))
        self.status.setText("已复制 %d 个 DOI：%s" % (len(dois), "；".join(dois)))

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
