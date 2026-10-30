# -*- coding: utf-8 -*-
"""方案 A：可选 PubMed 候选检索。内置引擎，一键检索；不改 S1，不下载全文。"""
from __future__ import annotations

import os
import time

from PySide6.QtCore import Qt, QThread, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMessageBox, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem, QTextBrowser,
    QVBoxLayout, QWidget,
)

from app import paths
from app.router_client import ENGINES, RouterWorker


class RouterDialog(QDialog):
    def __init__(self, scenario: dict, parent=None):
        super().__init__(parent)
        self.scenario = scenario
        self.thread = None
        self.worker = None
        self.candidate_path = None
        self.papers = []

        self.setWindowTitle("先找候选论文（PubMed）")
        self.resize(1040, 700)
        lay = QVBoxLayout(self)

        # ---- 运行方式：默认内置，一键检索，不需要单独启动服务 ----
        lay.addWidget(QLabel("检索方式"))
        top = QHBoxLayout()
        self.engine = QComboBox()
        for label, value in ENGINES:
            self.engine.addItem(label, value)
        # 老用户如果显式设了 HCCFIT_ROUTER_URL，默认走外部服务
        if os.environ.get("HCCFIT_ROUTER_URL"):
            idx = self.engine.findData("http")
            if idx >= 0:
                self.engine.setCurrentIndex(idx)
        self.engine.currentIndexChanged.connect(self._refresh_engine)
        top.addWidget(self.engine)

        self.external_row = QWidget()
        ext = QHBoxLayout(self.external_row)
        ext.setContentsMargins(0, 0, 0, 0)
        self.url = QLineEdit(os.environ.get("HCCFIT_ROUTER_URL", "http://127.0.0.1:8080"))
        self.token = QLineEdit()
        self.token.setEchoMode(QLineEdit.EchoMode.Password)
        self.token.setPlaceholderText("Router token（可选）")
        ext.addWidget(QLabel("地址"))
        ext.addWidget(self.url, 3)
        ext.addWidget(self.token, 2)
        top.addWidget(self.external_row, 1)
        lay.addLayout(top)

        self.engine_msg = QLabel("")
        self.engine_msg.setWordWrap(True)
        self.engine_msg.setStyleSheet("color:#66727f;font-size:9pt;")
        lay.addWidget(self.engine_msg)

        # ---- 检索参数：一键即可，Key 只放内存 ----
        row = QHBoxLayout()
        self.sp_key = QLineEdit()
        self.sp_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.sp_key.setPlaceholderText("SciencePal Key（仅 balanced；只放内存）")
        self.filter_agent = QLineEdit()
        self.filter_agent.setPlaceholderText("filter agent ID（仅 balanced，可选）")
        self.strategy = QComboBox()
        self.strategy.addItem("fast（5–10 秒）", "fast")
        self.strategy.addItem("balanced（60–90 秒）", "balanced")
        self.k = QSpinBox()
        self.k.setRange(1, 8)
        self.k.setValue(8)
        self.search_btn = QPushButton("一键检索候选")
        self.search_btn.clicked.connect(self.search)
        row.addWidget(self.sp_key, 2)
        row.addWidget(self.filter_agent, 2)
        row.addWidget(self.strategy)
        row.addWidget(QLabel("k"))
        row.addWidget(self.k)
        row.addWidget(self.search_btn)
        lay.addLayout(row)

        self.status = QLabel("只做候选发现；题录/摘要不是全文，S2 四维核查仍需 PDF。"
                             " balanced 需要 SciencePal Key（可填上方或配在环境变量），"
                             "以及 filter agent ID。")
        self.status.setWordWrap(True)
        self.status.setStyleSheet("color:#8a5a00;background:#fff5e0;border:1px solid #f0dcb0;"
                                  "border-radius:6px;padding:6px;")
        lay.addWidget(self.status)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["选", "标题", "年", "期刊", "可读性", "为什么 / 风险"])
        self.table.setWordWrap(True)
        self.table.setAlternatingRowColors(True)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        h.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for i in range(2, 6):
            h.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemDoubleClicked.connect(lambda _i: self.open_fulltext())
        lay.addWidget(self.table, 1)

        self.warn = QTextBrowser()
        self.warn.setFixedHeight(70)
        lay.addWidget(self.warn)

        bar = QHBoxLayout()
        self.full_btn = QPushButton("打开 PMC / PubMed")
        self.full_btn.clicked.connect(self.open_fulltext)
        self.doi_btn = QPushButton("复制 DOI")
        self.doi_btn.clicked.connect(self.copy_doi)
        self.add_btn = QPushButton("保存选中候选并加入论文列表")
        self.add_btn.setEnabled(False)
        self.add_btn.clicked.connect(self.save_candidates)
        bar.addWidget(self.full_btn)
        bar.addWidget(self.doi_btn)
        bar.addStretch(1)
        bar.addWidget(self.add_btn)
        lay.addLayout(bar)

        self._refresh_engine()

    # ---------------- 运行方式 ----------------
    def current_engine(self) -> str:
        return self.engine.currentData() or "builtin"

    def _refresh_engine(self):
        builtin = self.current_engine() == "builtin"
        self.external_row.setVisible(not builtin)
        if not builtin:
            self.engine_msg.setText("外部服务：需先启动 HCC-Fit_Simple_Router；"
                                    "不想启动服务就选“内置引擎”。")
            self.search_btn.setEnabled(True)
            return
        from app.router_engine import engine_status
        st = engine_status()
        if not st["available"]:
            self.engine_msg.setText("内置引擎不可用：%s" % st.get("error"))
            self.search_btn.setEnabled(False)
            return
        self.search_btn.setEnabled(True)
        if st.get("source") == "bundled":
            where = "（打包内置）"
        else:
            where = "（%s）" % st["path"] if st.get("path") else ""
        self.engine_msg.setText(
            "内置引擎可用%s · 策略 %s · SciencePal Key %s · filter agent %s"
            % (where, "/".join(st.get("strategies") or []),
               "已配置" if st.get("sciencepal_configured") else "未配置（balanced 请在上方填写）",
               "已配置" if st.get("filter_configured") else "未配置（balanced 会降级为 fast）"))

    # ---------------- 检索 ----------------
    def search(self):
        if self.thread is not None:
            return
        engine = self.current_engine()
        if engine == "http" and not self.url.text().strip():
            QMessageBox.warning(self, "缺少地址", "外部服务模式需要填写 Router 地址。")
            return
        self.search_btn.setEnabled(False)
        self.status.setText("检索中…（%s）" % ("内置引擎，无需服务" if engine == "builtin" else "外部服务"))
        self.thread = QThread(self)
        self.worker = RouterWorker(
            self.scenario, self.strategy.currentData(), self.k.value(),
            self.sp_key.text().strip(), engine=engine,
            base_url=self.url.text().strip(), token=self.token.text().strip(),
            filter_agent_id=self.filter_agent.text().strip(),
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
            self.table.setRowHeight(row, 48)
        warnings = data.get("warnings") or []
        degraded = (data.get("meta") or {}).get("degraded")
        self.warn.setPlainText("\n".join(warnings) or ("已降级：SciencePal 不可用" if degraded else "无警告"))
        used = data.get("strategy_used") or ""
        self.status.setText("返回 %d 篇，实际策略：%s（%s）；勾选后保存，再回论文区添加 PDF。"
                            % (len(self.papers), used or "—",
                               "内置引擎" if self.current_engine() == "builtin" else "外部服务"))
        self.add_btn.setEnabled(bool(self.papers))
        if not self.papers:
            self.status.setText("没有找到候选；可改用 fast、放宽条件，或直接在论文区添加 PDF。")

    def _on_failed(self, msg: str):
        self.search_btn.setEnabled(True)
        self.status.setText("检索失败：%s" % msg)
        self.warn.setPlainText(msg)

    # ---------------- 候选操作 ----------------
    def _row_paper(self):
        row = self.table.currentRow()
        if row < 0:
            return None
        cell = self.table.item(row, 0)
        return cell.data(Qt.ItemDataRole.UserRole) if cell else None

    def open_fulltext(self):
        p = self._row_paper()
        if not p:
            return
        rd = p.get("readability") or {}
        url = rd.get("url") or (
            "https://pubmed.ncbi.nlm.nih.gov/%s/" % p.get("pmid") if p.get("pmid") else "")
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def copy_doi(self):
        p = self._row_paper()
        if p and p.get("doi"):
            QApplication.clipboard().setText(str(p["doi"]))

    def save_candidates(self):
        selected = self._selected_papers()
        if not selected:
            QMessageBox.warning(self, "未选择", "请至少勾选一篇候选。")
            return
        out_dir = paths.app_data_dir() / "router_candidates"
        out_dir.mkdir(parents=True, exist_ok=True)
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
        path.write_text("\n".join(lines), encoding="utf-8")
        self.candidate_path = str(path)
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
        self.sp_key.clear()
        event.accept()
