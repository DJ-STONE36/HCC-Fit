# -*- coding: utf-8 -*-
"""HCC-Fit 桌面主窗口（PySide6）。"""
from __future__ import annotations

import time
from html import escape
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QFileDialog, QGroupBox,
    QHBoxLayout, QHeaderView, QLabel, QLineEdit, QListWidget, QListWidgetItem, QMainWindow,
    QMessageBox, QPlainTextEdit, QProgressBar, QPushButton, QScrollArea,
    QSplitter, QTabWidget, QTableWidget, QTableWidgetItem, QTextBrowser,
    QTextEdit, QVBoxLayout, QWidget,
)

from app import export, input_store, links, paths, supplement_store
from app.input_review import InputReviewDialog
from app.router_client import RouterError, from_session
from app.router_dialog import RouterDialog
from app.supplement import (Supplement, build_supplemented_scenario,
                            resume_target, with_doc_texts)
from app.input_normalization import RuleExtractor
from app.workers import ProbeWorker, RunWorker

MODES = [
    ("SciencePal Agent（五阶段）", "sciencepal"),
    ("SciencePal · HCC-Fit 单次报告", "sciencepal_hcc"),
]
TIMEPOINTS = [
    ("术前（切除/消融前）", "pre_surgery"),
    ("治疗前（TACE/系统治疗）", "pre_treatment"),
    ("术后", "post_surgery"),
    ("复发后", "at_recurrence"),
    ("随访", "surveillance"),
]
STAGES_5 = [
    ("domain", "S0 域判定"),
    ("papers", "S1 论文选择"),
    ("match", "S2 逐篇匹配"),
    ("integrate", "S3 综合方案"),
    ("expected", "S4 期望数据"),
]
STAGE_HCC = [("hcc", "HCC-Fit 单次核查")]
STATUS_TEXT = {"match": "匹配", "mismatch": "不匹配", "unknown": "无法确认"}
TIER_TEXT = {"applicable": "可直接讨论", "conditional": "有条件可用",
             "undetermined": "需补数据 / 无法确认", "not_applicable": "不适用"}
STRENGTH_TEXT = {"strong": "强", "moderate": "中", "weak": "弱", "insufficient": "不足"}
DIM_TEXT = {"population": "人群", "timepoint": "时点", "input": "输入", "outcome": "结局"}
STATUS_COLOR = {
    "match": ("#1e8449", "#eafaf1"),
    "mismatch": ("#c0392b", "#fdedec"),
    "unknown": ("#b9770e", "#fef5e7"),
}


class FileDropList(QListWidget):
    filesDropped = Signal(list)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(True)
        self.setMinimumHeight(90)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        files = []
        for url in event.mimeData().urls():
            if url.isLocalFile():
                files.append(url.toLocalFile())
        if files:
            self.filesDropped.emit(files)
            event.acceptProposedAction()


class MainWindow(QMainWindow):
    runFinished = Signal()

    def __init__(self):
        super().__init__()
        self.files = []
        self.supplement_files = []       # 补充资料阶段的 PDF 附件（原样上传给 Agent）
        self.visit = None
        self.thread = None
        self.worker = None
        self.probe_thread = None
        self.probe_worker = None
        self.workspace = None
        self.input_session = None
        self.input_confirmed = False
        self.confirmed_scenario = None
        self.previous_visit = None
        self._supplement_parent = None
        self._supplement_record = None
        self._setting_scene = False
        self.run_started = 0.0
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self._tick)

        self.setWindowTitle("HCC-Fit · 肝细胞癌预测模型场景适配核查")
        self.resize(1420, 900)
        self._build_ui()
        self._load_settings()
        self._on_mode_changed()
        self._check_key()

    # ---------------- UI ----------------
    def _build_ui(self):
        root = QWidget(self)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(10, 8, 10, 8)

        header = QHBoxLayout()
        title = QLabel("HCC·Fit")
        title.setFont(QFont("Microsoft YaHei", 16, QFont.Weight.Bold))
        sub = QLabel("需求 + 病人基础 → 读论文 → 四维核查 → 就诊反馈")
        sub.setStyleSheet("color:#66727f;")
        self.badge = QLabel("演示模式")
        self.badge.setStyleSheet("background:#3a2f12;color:#f5d38a;padding:3px 10px;border-radius:10px;")
        header.addWidget(title)
        header.addWidget(sub)
        header.addStretch(1)
        header.addWidget(self.badge)
        outer.addLayout(header)

        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.addWidget(self._build_left())
        self.split.addWidget(self._build_middle())
        self.split.addWidget(self._build_right())
        self.split.setSizes([390, 300, 730])
        outer.addWidget(self.split, 1)
        self.setCentralWidget(root)
        self.statusBar().showMessage("就绪")

    def _build_left(self):
        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body = QWidget()
        lay = QVBoxLayout(body)
        lay.setContentsMargins(2, 2, 8, 2)

        scene = QGroupBox("2 · 场景")
        sv = QVBoxLayout(scene)
        sv.addWidget(QLabel("请描述患者情况和你想回答的问题（一段话即可）"))
        ex_row = QHBoxLayout()
        self.scene_example = QLabel(
            "示例：58 岁男性，乙肝，肝癌，刚做完切除，病理还没出来，从今天开始，想看看三年总生存率；如果做模型核查，请说明是否 HCC、切除是否根治。")
        self.scene_example.setWordWrap(True)
        self.scene_example.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.scene_example.setStyleSheet("color:#66727f;font-size:9pt;")
        self.example_btn = QPushButton("填入示例")
        self.example_btn.clicked.connect(self._fill_scene_example)
        ex_row.addWidget(self.scene_example, 1)
        ex_row.addWidget(self.example_btn, 0, Qt.AlignmentFlag.AlignTop)
        sv.addLayout(ex_row)
        self.scene_input = QTextEdit()
        self.scene_input.setPlaceholderText("把患者情况、治疗、阶段、想问的问题写成一段话……")
        self.scene_input.setFixedHeight(130)
        self.scene_input.textChanged.connect(self._on_scene_text_changed)
        sv.addWidget(self.scene_input)
        row = QHBoxLayout()
        self.parse_btn = QPushButton("解析并整理成场景卡")
        self.parse_btn.clicked.connect(self.open_input_review)
        self.scene_status = QLabel("尚未整理")
        self.scene_status.setWordWrap(True)
        self.scene_status.setStyleSheet("color:#8a5a00;")
        row.addWidget(self.parse_btn)
        row.addWidget(self.scene_status, 1)
        sv.addLayout(row)

        # 兼容旧流程的隐藏字段：只由“确认后的场景卡”写入，不直接在界面显示
        self.patient = QTextEdit(scene)
        self.patient.hide()
        self.task = QTextEdit(scene)
        self.task.hide()
        self.timepoint = QComboBox(scene)
        for label, value in TIMEPOINTS:
            self.timepoint.addItem(label, value)
        self.timepoint.hide()
        self.outcome = QLineEdit(scene)
        self.outcome.hide()
        self.given = QLineEdit(scene)
        self.given.hide()
        self.missing = QLineEdit(scene)
        self.missing.hide()

        files = QGroupBox("3 · 论文")
        fv = QVBoxLayout(files)
        fv.addWidget(QLabel("论文关键段落（可选，但很建议）"))
        self.paper_text = QTextEdit()
        self.paper_text.setPlaceholderText("把摘要 / 方法 / 结论粘进来；只上传 PDF 也可以。")
        self.paper_text.setFixedHeight(70)
        fv.addWidget(self.paper_text)
        self.file_list = FileDropList()
        self.file_list.filesDropped.connect(self.add_files)
        fv.addWidget(self.file_list)
        fh = QHBoxLayout()
        self.pick_btn = QPushButton("添加文件")
        self.pick_btn.clicked.connect(self.pick_files)
        self.clear_btn = QPushButton("清空")
        self.clear_btn.clicked.connect(self.clear_files)
        self.file_info = QLabel("未选择文件")
        self.file_info.setStyleSheet("color:#66727f;")
        fh.addWidget(self.pick_btn)
        fh.addWidget(self.clear_btn)
        fh.addWidget(self.file_info, 1)
        fv.addLayout(fh)
        rh = QHBoxLayout()
        self.router_btn = QPushButton("找候选论文（内置 PubMed）")
        self.router_btn.setToolTip("内置 Router 引擎，一键检索；不需要单独启动服务")
        self.router_btn.clicked.connect(self.open_router_dialog)
        rh.addWidget(self.router_btn)
        rh.addStretch(1)
        fv.addLayout(rh)
        self.split_papers = QCheckBox("多篇时逐篇发给 Agent（较慢）")
        self.split_papers.setToolTip(
            "勾选后：五阶段模式对每篇论文单独做 S2 匹配；单次核查模式对每篇论文单独出报告，"
            "结果合并到同一份 visit。不勾选则所有论文一起发给 Agent。")
        fv.addWidget(self.split_papers)

        brain = QGroupBox("1 · SciencePal 连接")
        bv = QVBoxLayout(brain)
        self.mode = QComboBox()
        for label, value in MODES:
            self.mode.addItem(label, value)
        self.mode.currentIndexChanged.connect(self._on_mode_changed)
        bv.addWidget(self.mode)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("SciencePal API Key（只放内存，不写盘）")
        bv.addWidget(self.key)
        self.key_warn = QLabel("")
        self.key_warn.setWordWrap(True)
        self.key_warn.setStyleSheet("color:#c0392b;font-size:9pt;")
        bv.addWidget(self.key_warn)
        self.key.textChanged.connect(self._check_key)
        built = QLabel("连接：https://sciencepal.ai　·　Agent：HCC-Fit（内置，无需填写）")
        built.setWordWrap(True)
        built.setStyleSheet("color:#66727f;font-size:9pt;")
        bv.addWidget(built)
        ph = QHBoxLayout()
        self.probe_btn = QPushButton("测试连接 / 看 Agent")
        self.probe_btn.clicked.connect(self.probe)
        self.probe_msg = QLabel("")
        self.probe_msg.setWordWrap(True)
        self.probe_msg.setStyleSheet("color:#66727f;")
        ph.addWidget(self.probe_btn)
        ph.addWidget(self.probe_msg, 1)
        bv.addLayout(ph)

        self.start_btn = QPushButton("开始就诊流程")
        self.start_btn.setMinimumHeight(40)
        self.start_btn.setStyleSheet("background:#0f1722;color:white;font-weight:600;border-radius:8px;")
        self.start_btn.clicked.connect(self.start_run)

        foot = QLabel("不诊断、不推荐治疗、不接触患者身份信息。结果需人工复核。")
        foot.setWordWrap(True)
        foot.setStyleSheet("color:#8a97a6;font-size:9pt;")

        lay.addWidget(brain)
        lay.addWidget(scene)
        lay.addWidget(files)
        lay.addStretch(1)
        scroll.setWidget(body)
        outer.addWidget(scroll, 1)
        outer.addWidget(self.start_btn)
        outer.addWidget(foot)
        return container

    def _build_middle(self):
        box = QGroupBox("4 · 流程")
        v = QVBoxLayout(box)
        self.stage_list = QListWidget()
        self.stage_list.setAlternatingRowColors(True)
        self.stage_list.setWordWrap(True)
        self.stage_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        v.addWidget(self.stage_list, 1)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setVisible(False)
        v.addWidget(self.progress)
        self.error = QLabel("")
        self.error.setWordWrap(True)
        self.error.setStyleSheet("background:#fdedec;color:#c0392b;border:1px solid #f5b7b1;"
                                 "border-radius:6px;padding:8px;")
        self.error.setVisible(False)
        v.addWidget(self.error)
        self.workspace_label = QLabel("")
        self.workspace_label.setWordWrap(True)
        self.workspace_label.setMinimumHeight(36)
        self.workspace_label.setStyleSheet("color:#8a97a6;font-size:9pt;")
        v.addWidget(self.workspace_label)
        self.open_ws_btn = QPushButton("打开工作区（日志 / 原始返回 / 人工文件）")
        self.open_ws_btn.setEnabled(False)
        self.open_ws_btn.clicked.connect(self.open_workspace)
        v.addWidget(self.open_ws_btn)
        return box

    def _build_right(self):
        box = QGroupBox("5 · 就诊反馈")
        v = QVBoxLayout(box)
        top = QHBoxLayout()
        self.report_switch = QComboBox()
        self.report_switch.setVisible(False)
        self.report_switch.currentIndexChanged.connect(self._on_report_switch)
        top.addWidget(self.report_switch)
        self.pdf_btn = QPushButton("导出 PDF（给人看）")
        self.json_btn = QPushButton("导出 JSON（给 Agent）")
        self.pdf_btn.setEnabled(False)
        self.json_btn.setEnabled(False)
        self.pdf_btn.clicked.connect(self.export_pdf)
        self.json_btn.clicked.connect(self.export_json)
        top.addWidget(self.pdf_btn)
        top.addWidget(self.json_btn)
        top.addStretch(1)
        self.show_raw = QCheckBox("显示原始返回")
        self.show_raw.setToolTip("默认隐藏；只有需要排错/审计时再显示。原始内容始终保存在 visit.json。")
        self.show_raw.toggled.connect(self._apply_raw_visibility)
        top.addWidget(self.show_raw)
        v.addLayout(top)

        self.tabs = QTabWidget()
        self.domain = QLabel("还没有结果。")
        self.domain.setWordWrap(True)
        self.fallback = QLabel("")
        self.fallback.setWordWrap(True)
        self.fallback.setStyleSheet("background:#fff4e0;color:#8a5a00;border:1px solid #f0dcb0;"
                                    "border-radius:6px;padding:6px;")
        self.fallback.setVisible(False)
        self.repair_note = QLabel("")
        self.repair_note.setWordWrap(True)
        self.repair_note.setStyleSheet("background:#eafaf1;color:#1e8449;border:1px solid #bfe3d6;"
                                       "border-radius:6px;padding:6px;")
        self.repair_note.setVisible(False)
        self.summary = QTextBrowser()
        self.summary.setOpenExternalLinks(False)
        self.cautions = QTextBrowser()
        conclude = QWidget()
        cv = QVBoxLayout(conclude)
        cv.addWidget(self.domain)
        cv.addWidget(self.fallback)
        cv.addWidget(self.repair_note)
        cv.addWidget(QLabel("综合结论"))
        cv.addWidget(self.summary, 2)
        cv.addWidget(QLabel("注意点"))
        cv.addWidget(self.cautions, 1)
        self.tabs.addTab(conclude, "结论")

        matrix_page = QWidget()
        mv = QVBoxLayout(matrix_page)
        self.matrix = QTableWidget(0, 7)
        self.matrix.setHorizontalHeaderLabels(["论文", "档位", "证据", "人群", "时点", "输入", "结局"])
        self.matrix.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.matrix.setWordWrap(True)
        header = self.matrix.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for i in range(1, 7):
            header.setSectionResizeMode(i, QHeaderView.ResizeMode.ResizeToContents)
        mv.addWidget(self.matrix, 1)
        self.detail = QTextBrowser()
        self.detail.setOpenExternalLinks(False)
        mv.addWidget(QLabel("逐维理由与原文引文"))
        mv.addWidget(self.detail, 1)
        self.tabs.addTab(matrix_page, "四维")

        misc_page = QWidget()
        mv2 = QVBoxLayout(misc_page)
        self.misc = QTextBrowser()
        self.misc.setOpenExternalLinks(False)
        mv2.addWidget(self.misc)
        self.tabs.addTab(misc_page, "论文 / 方案 / 期望")

        self.raw_page = QWidget()
        rv = QVBoxLayout(self.raw_page)
        self.raw = QPlainTextEdit()
        self.raw.setReadOnly(True)
        self.raw.setPlaceholderText("原始返回默认隐藏；需要审计/排错时勾选右上角“显示原始返回”。")
        rv.addWidget(self.raw)
        # 不默认 addTab：由 _apply_raw_visibility 决定是否显示

        sup_page = QWidget()
        sv = QVBoxLayout(sup_page)
        sv.addWidget(QLabel("报告认为还缺这些（来自 expected_data）"))
        self.supplement_expected = QTextBrowser()
        self.supplement_expected.setReadOnly(True)
        self.supplement_expected.setOpenExternalLinks(True)
        self.supplement_expected.setFixedHeight(110)
        self.supplement_expected.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.supplement_expected.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        sv.addWidget(self.supplement_expected)
        sv.addWidget(QLabel("补充资料（每项一条，一一对应）"))
        self.supplement_items_area = QScrollArea()
        self.supplement_items_area.setWidgetResizable(True)
        self.supplement_items_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.supplement_items_area.setFixedHeight(230)
        self.supplement_items_body = QWidget()
        self.supplement_items_layout = QVBoxLayout(self.supplement_items_body)
        self.supplement_items_layout.setContentsMargins(2, 2, 4, 2)
        self.supplement_items_area.setWidget(self.supplement_items_body)
        sv.addWidget(self.supplement_items_area)
        self.supplement_inputs = []

        pdf_row = QHBoxLayout()
        self.supplement_pdf_btn = QPushButton("上传 PDF 附件（可选）")
        self.supplement_pdf_btn.setToolTip(
            "上传病理 / 化验 / 影像报告等 PDF：原样作为附件发给 Agent（不抽文本），"
            "并附提示词说明这些是患者资料、不是论文。")
        self.supplement_pdf_btn.clicked.connect(self.pick_supplement_pdfs)
        self.supplement_pdf_clear = QPushButton("清空附件")
        self.supplement_pdf_clear.setEnabled(False)
        self.supplement_pdf_clear.clicked.connect(self.clear_supplement_files)
        pdf_row.addWidget(self.supplement_pdf_btn)
        pdf_row.addWidget(self.supplement_pdf_clear)
        pdf_row.addStretch(1)
        sv.addLayout(pdf_row)
        self.supplement_files_list = QListWidget()
        self.supplement_files_list.setFixedHeight(58)
        self.supplement_files_list.setVisible(False)
        sv.addWidget(self.supplement_files_list)
        self.supplement_new_session = QCheckBox(
            "为附件新开会话（会丢掉上一轮上下文；只在必须原样上传 PDF 时勾选）")
        self.supplement_new_session.setToolTip(
            "默认沿用原会话：平台的追问接口只能发文字，PDF 会先抽出文字随消息附上。\n"
            "勾选后改为新开会话、把 PDF 原样上传，但 Agent 看不到上一轮的对话。")
        self.supplement_new_session.setVisible(False)
        sv.addWidget(self.supplement_new_session)
        self.supplement_status = QLabel("先完成一次核查，报告会列出缺失项。")
        self.supplement_status.setWordWrap(True)
        self.supplement_status.setStyleSheet("color:#66727f;")
        sv.addWidget(self.supplement_status)
        self.supplement_btn = QPushButton("重新提交分析（全量重跑）")
        self.supplement_btn.setMinimumHeight(36)
        self.supplement_btn.setEnabled(False)
        self.supplement_btn.clicked.connect(self.start_supplement_run)
        sv.addWidget(self.supplement_btn)
        self.supplement_page = sup_page
        self.tabs.addTab(sup_page, "补充资料")

        v.addWidget(self.tabs, 1)
        return box

    # ---------------- 模式 ----------------
    def current_mode(self):
        return self.mode.currentData() or "sciencepal"

    def select_mode(self, mode: str) -> bool:
        """按 mode id 选大脑。

        界面只提供 sciencepal / sciencepal_hcc；其它 id（如自动化测试用的 stub）
        runner 仍支持，但不在下拉框里展示，这里按需临时加入。
        """
        idx = self.mode.findData(mode)
        if idx < 0:
            self.mode.addItem(mode, mode)
            idx = self.mode.count() - 1
        self.mode.setCurrentIndex(idx)
        return True

    def _on_mode_changed(self):
        if not hasattr(self, "stage_list") or not hasattr(self, "key"):
            return
        mode = self.current_mode()
        sp = mode in ("sciencepal", "sciencepal_hcc")
        for w in (self.key, self.probe_btn):
            w.setEnabled(sp)
        if mode == "sciencepal_hcc":
            self.badge.setText("SciencePal · HCC-Fit 报告")
            self.badge.setStyleSheet("background:#12352a;color:#9fe3c4;padding:3px 10px;border-radius:10px;")
        elif mode == "sciencepal":
            self.badge.setText("SciencePal Agent")
            self.badge.setStyleSheet("background:#12352a;color:#9fe3c4;padding:3px 10px;border-radius:10px;")
        elif mode == "stub":
            self.badge.setText("离线自检（仅自动化测试）")
            self.badge.setStyleSheet("background:#3a2f12;color:#f5d38a;padding:3px 10px;border-radius:10px;")
        else:
            self.badge.setText(mode)
        self._rebuild_stages()

    def _rebuild_stages(self):
        self.stage_list.clear()
        rows = STAGE_HCC if self.current_mode() == "sciencepal_hcc" else STAGES_5
        for key, label in rows:
            item = QListWidgetItem("%s　·　待开始" % label)
            item.setData(Qt.ItemDataRole.UserRole, key)
            item.setData(Qt.ItemDataRole.UserRole + 1, label)
            self.stage_list.addItem(item)

    # ---------------- 文件 ----------------
    def add_files(self, paths_):
        for p in paths_:
            if p not in self.files:
                self.files.append(p)
        self._refresh_files()

    def clear_files(self):
        self.files = []
        self._refresh_files()

    def _refresh_files(self):
        self.file_list.clear()
        for p in self.files:
            try:
                size = Path(p).stat().st_size / 1024.0
            except OSError:
                size = 0
            self.file_list.addItem("%s　·　%.0f KB" % (Path(p).name, size))
        self.file_info.setText("已选 %d 个文件" % len(self.files) if self.files else "未选择文件")

    def open_router_dialog(self):
        if not self.input_session or not self.input_confirmed:
            QMessageBox.warning(self, "先确认场景卡",
                                "请先解析并确认场景卡，再用 Router 检索 PubMed 候选。")
            return
        try:
            scenario = from_session(self.input_session, self.confirmed_scenario)
        except RouterError as exc:
            QMessageBox.warning(self, "Router 不适用", str(exc))
            return
        dlg = RouterDialog(scenario, self.key.text().strip(), self)
        if dlg.exec() and dlg.candidate_path:
            self.add_files([dlg.candidate_path])
            self.file_info.setText(
                "已加入候选清单（题录/摘要，不是全文）；有 PDF 请再添加")

    def pick_files(self):
        files, _ = QFileDialog.getOpenFileNames(self, "选择论文 / 数据", "",
                                                "论文或文本 (*.pdf *.md *.txt *.docx);;所有文件 (*)")
        if files:
            self.add_files(files)

    # ---------------- 单输入场景卡 ----------------
    def _fill_scene_example(self):
        self.scene_input.setPlainText(
            "58 岁男性，乙肝，肝癌，刚做完切除，病理还没出来，从今天开始，想看看三年总生存率；如果做模型核查，请说明是否 HCC、切除是否根治。")

    def _on_scene_text_changed(self):
        if self._setting_scene:
            return
        if self.input_confirmed or self.confirmed_scenario:
            self.input_confirmed = False
            self.confirmed_scenario = None
            self.scene_status.setText("内容已修改：请重新解析并确认场景卡")
            self.scene_status.setStyleSheet("color:#c0392b;")

    def open_input_review(self):
        raw = self.scene_input.toPlainText().strip()
        if not raw:
            QMessageBox.warning(self, "缺少场景描述",
                                "请先用一段话描述患者情况和你想回答的问题。")
            return
        dlg = InputReviewDialog(raw, paths.resource("terms.csv"), self)
        if dlg.exec():
            self._apply_input_session(dlg.confirmed_session)

    def _apply_input_session(self, session):
        self.input_session = session
        self.input_confirmed = session.confirmation.get("status") == "user_confirmed"
        self.confirmed_scenario = RuleExtractor.to_agent_scenario(
            session, self.paper_text.toPlainText().strip())
        sc = self.confirmed_scenario
        self._setting_scene = True
        try:
            self.patient.setPlainText(sc.get("patient_summary_zh", ""))
            self.task.setPlainText(sc.get("task_zh", ""))
            self.outcome.setText(sc.get("outcome_type", ""))
            self.given.setText(sc.get("inputs_given", ""))
            self.missing.setText(sc.get("inputs_missing", ""))
            idx = self.timepoint.findData(sc.get("use_timepoint"))
            if idx >= 0:
                self.timepoint.setCurrentIndex(idx)
        finally:
            self._setting_scene = False
        stored = ""
        try:
            stored = str(input_store.save(session))
        except Exception as exc:  # noqa: BLE001
            stored = "（草稿未保存：%s）" % type(exc).__name__
        self.scene_status.setText("已确认 · %s · v%d" % (session.input_id, session.version))
        self.scene_status.setStyleSheet("color:#1e8449;")
        self.scene_status.setToolTip(stored)

    # ---------------- 运行 ----------------
    def _collect_scenario(self):
        if self.confirmed_scenario:
            sc = dict(self.confirmed_scenario)
            sc["paper_text"] = self.paper_text.toPlainText().strip()
            return sc
        return {
            "patient_summary_zh": self.patient.toPlainText().strip(),
            "task_zh": self.task.toPlainText().strip(),
            "use_timepoint": self.timepoint.currentData(),
            "outcome_type": self.outcome.text().strip(),
            "inputs_given": self.given.text().strip(),
            "inputs_missing": self.missing.text().strip(),
            "paper_text": self.paper_text.toPlainText().strip(),
        }

    def start_run(self):
        if self.thread is not None:
            return
        if not self.input_confirmed or not self.confirmed_scenario:
            self.open_input_review()
            if not self.input_confirmed:
                return
        scenario = self._collect_scenario()
        if not scenario["patient_summary_zh"] or not scenario["task_zh"]:
            QMessageBox.warning(self, "缺少输入", "场景卡里缺少患者摘要或问题，请重新整理。")
            return
        mode = self.current_mode()
        level, key_msg = self._check_key()
        if mode in ("sciencepal", "sciencepal_hcc") and level == "error":
            QMessageBox.warning(self, "SciencePal Key 有问题",
                                key_msg + "\n\n请在上方“1 · 大脑 / SciencePal 连接”里填写或重新复制。")
            return
        if mode == "sciencepal_hcc" and not self.files and not scenario["paper_text"]:
            QMessageBox.warning(self, "缺少论文", "HCC-Fit 单次核查需要上传 PDF 或粘贴正文。")
            return

        try:
            sid = paths.new_sid()
            ws = paths.workspace(sid)
            names = paths.stage_files(self.files, ws)
        except Exception as exc:  # noqa: BLE001
            self.error.setText("工作区失败：%s: %s" % (type(exc).__name__, exc))
            self.error.setVisible(True)
            self.statusBar().showMessage("工作区失败")
            return

        self.workspace = ws
        self.workspace_label.setText("工作区：%s" % ws)
        self.workspace_label.setToolTip(str(ws))
        self.open_ws_btn.setEnabled(True)
        self.error.setVisible(False)
        self.progress.setVisible(True)
        self.start_btn.setEnabled(False)
        self.pdf_btn.setEnabled(False)
        self.json_btn.setEnabled(False)
        self.previous_visit = None
        self.report_switch.setVisible(False)
        self._supplement_parent = None
        self._supplement_record = None
        self.visit = None
        self._rebuild_stages()
        self.run_started = time.time()
        self.timer.start()
        self.statusBar().showMessage("运行中…")
        self.tabs.setCurrentIndex(0)
        self._launch_worker(mode, scenario, names, ws)

    def _launch_worker(self, mode, scenario, names, ws, attachments=None,
                       thread_id=None, resume=False):
        self._rebuild_stages()               # 清掉上一次运行的逐篇行
        opts = {
            "api_key": self.key.text().strip(),
            "timeout": 240 if mode == "sciencepal_hcc" else 180,
            "split_papers": self.split_papers.isChecked(),
            "attachments": list(attachments or []),
            "thread_id": str(thread_id or ""),
            "resume": bool(resume),
        }
        self.thread = QThread(self)
        self.worker = RunWorker(mode, str(ws), scenario, names, opts)
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.stage.connect(self._on_stage)
        self.worker.done.connect(self._on_done)
        self.worker.failed.connect(self._on_failed)
        self.worker.done.connect(self.thread.quit)
        self.worker.failed.connect(self.thread.quit)
        self.thread.finished.connect(self._thread_finished)
        self.thread.start()

    # ---------------- 报告后补充资料 ----------------
    def _set_report_switch(self, current, previous=None):
        self.report_switch.blockSignals(True)
        self.report_switch.clear()
        self.report_switch.addItem("本次报告", current)
        if previous:
            self.report_switch.addItem("上一次报告", previous)
            self.report_switch.setVisible(True)
        else:
            self.report_switch.setVisible(False)
        self.report_switch.setCurrentIndex(0)
        self.report_switch.blockSignals(False)

    def _on_report_switch(self):
        v = self.report_switch.currentData()
        if v:
            self.visit = v
            self.pdf_btn.setEnabled(True)
            self.json_btn.setEnabled(True)
            self._render(v)

    def _clear_supplement_inputs(self):
        while self.supplement_items_layout.count():
            item = self.supplement_items_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
        self.supplement_inputs = []

    def _render_supplement(self, v):
        items = v.get("expected_data") or []
        html = ["<ul>"]
        for e in items:
            html.append("<li><b>%s</b>" % escape(str(e.get("item_zh") or "")))
            if e.get("why_zh"):
                html.append("<br><span style='color:#66727f'>%s</span>" % escape(str(e["why_zh"])))
            if e.get("unlocks_zh"):
                html.append("<br><span style='color:#8a97a6'>补齐可解锁：%s</span>"
                            % escape(str(e["unlocks_zh"])))
            html.append("</li>")
        html.append("</ul>")
        self.supplement_expected.setHtml(
            "".join(html) if items else "<p>报告没有列出明确缺失项；仍可在下面补充资料。</p>")

        self._clear_supplement_inputs()
        for e in items:
            label = QLabel("• " + str(e.get("item_zh") or ""))
            label.setWordWrap(True)
            label.setStyleSheet("font-weight:600;")
            if e.get("why_zh"):
                label.setToolTip(str(e["why_zh"]))
            edit = QTextEdit()
            edit.setPlaceholderText("填写这一项资料（没有就留空）")
            edit.setFixedHeight(52)
            edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            edit.textChanged.connect(self._validate_supplement)
            self.supplement_items_layout.addWidget(label)
            self.supplement_items_layout.addWidget(edit)
            self.supplement_inputs.append((e, edit))

        if not items:
            label = QLabel("• 其他补充")
            label.setStyleSheet("font-weight:600;")
            edit = QTextEdit()
            edit.setPlaceholderText("报告未列出缺失项，可在这里补充资料")
            edit.setFixedHeight(64)
            edit.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
            edit.textChanged.connect(self._validate_supplement)
            self.supplement_items_layout.addWidget(label)
            self.supplement_items_layout.addWidget(edit)
            self.supplement_inputs.append((None, edit))

        self.supplement_items_layout.addStretch(1)
        self.supplement_status.setText(
            "当前报告：%s；补充后全量重跑，原报告保留。" % (v.get("visit_id") or ""))
        self._validate_supplement()

    # ---------------- 补充资料：PDF 附件 ----------------
    def pick_supplement_pdfs(self):
        files, _ = QFileDialog.getOpenFileNames(
            self, "选择补充资料 PDF", "", "PDF (*.pdf);;所有文件 (*)")
        if files:
            self.add_supplement_pdfs(files)

    def add_supplement_pdfs(self, paths_):
        """加入补充资料 PDF：原样随重跑上传给 Agent，本地不抽文本。"""
        for p in paths_:
            if any(f["path"] == p for f in self.supplement_files):
                continue
            self.supplement_files.append({"path": str(p), "name": Path(p).name})
        self._refresh_supplement_files()
        self._validate_supplement()

    def clear_supplement_files(self):
        self.supplement_files = []
        self._refresh_supplement_files()
        self._validate_supplement()

    def _refresh_supplement_files(self):
        self.supplement_files_list.clear()
        for f in self.supplement_files:
            try:
                size = Path(f["path"]).stat().st_size / 1024.0 / 1024.0
                size_txt = "%.1f MB" % size
            except OSError:
                size_txt = "?"
            item = QListWidgetItem("%s　·　作为附件发送（%s）" % (f["name"], size_txt))
            item.setToolTip(f["path"])
            self.supplement_files_list.addItem(item)
        has = bool(self.supplement_files)
        self.supplement_files_list.setVisible(has)
        self.supplement_pdf_clear.setEnabled(has)
        self.supplement_new_session.setVisible(has)

    def _typed_supplement_text(self):
        lines = []
        for item, edit in self.supplement_inputs:
            val = edit.toPlainText().strip()
            if not val:
                continue
            name = str(item.get("item_zh") or "其他补充") if item else "其他补充"
            lines.append("%s：%s" % (name, val))
        return "\n".join(lines)

    def _supplement_text(self):
        return self._typed_supplement_text()

    def _supplement_plan(self):
        """(是否沿用原会话, thread_id, 原工作区路径)。

        默认沿用原会话（不新开会话）；只有勾选“为附件新开会话”且确实带了附件时才新开。
        """
        tid, ws = resume_target(self.visit)
        if not tid:
            return False, "", ws
        if self.supplement_files and self.supplement_new_session.isChecked():
            return False, tid, ws
        return True, tid, ws

    def _supplement_doc_texts(self):
        """同会话里只能发文字：把附件抽成文字（抽不到的进 bad，由界面提示）。"""
        from pdftext import extract

        docs, bad = [], []
        for f in self.supplement_files:
            body, _note = extract(f["path"], limit=3000)
            docs.append({"name": f["name"], "text": body})
            if not body:
                bad.append(f["name"])
        return docs, bad

    def _validate_supplement(self):
        if not hasattr(self, "supplement_btn"):
            return
        text = self._supplement_text().strip()
        docs = len(self.supplement_files)
        if not self.visit:
            self.supplement_btn.setEnabled(False)
            return
        if self.thread is not None:
            self.supplement_btn.setEnabled(False)
            self.supplement_status.setText("正在运行，结束后才能补充。")
            return
        if len(text) < 10 and not docs:
            self.supplement_btn.setEnabled(False)
            self.supplement_status.setText(
                "至少填写一项、合计 10 个字，或上传 PDF 附件后才能重新提交；原报告会保留。")
            return
        self.supplement_btn.setEnabled(True)
        reuse, tid, _ws = self._supplement_plan()
        if reuse:
            how = "在同一会话里追问（不新开会话）"
        elif tid:
            how = "新开会话（已勾选“为附件新开会话”）"
        else:
            how = "新开会话（原报告没有会话 ID，无法续话）"
        extra = "，附 %d 个 PDF" % docs if docs else ""
        self.supplement_status.setText(
            "将%s%s：原场景 + 补充资料；原报告保留。" % (how, extra))

    def start_supplement_run(self):
        if self.thread is not None or not self.visit:
            return
        text = self._supplement_text().strip()
        docs = [f["path"] for f in self.supplement_files]
        if len(text) < 10 and not docs:
            QMessageBox.warning(self, "补充太少",
                                "请至少填写一项、合计 10 个字的补充资料，或上传 PDF 附件。")
            return
        parent = self.visit
        mode = parent.get("mode") or self.current_mode()
        idx = self.mode.findData(mode)
        if idx >= 0:
            self.mode.setCurrentIndex(idx)
        if mode in ("sciencepal", "sciencepal_hcc") and not self.key.text().strip():
            QMessageBox.warning(self, "缺少 Key", "补充重跑沿用 SciencePal 模式，需要填 API Key。")
            return

        reuse, tid, parent_ws = self._supplement_plan()
        attach_names = []
        if reuse and docs:
            # 同一会话的追问接口只能发文字（平台限制）：抽文字随消息附上
            doc_texts, bad = self._supplement_doc_texts()
            if bad and not text:
                QMessageBox.warning(
                    self, "附件读不出来",
                    "同一会话里不能追加文件，而这些 PDF 也没抽出文字：\n\n- "
                    + "\n- ".join(bad)
                    + "\n\n请把关键内容粘贴到输入框，或勾选“为附件新开会话”后重试。")
                return
            if bad:
                ans = QMessageBox.question(
                    self, "有附件读不出来",
                    "同一会话里不能追加文件，以下 PDF 没抽出文字，本次不会带上它们：\n\n- "
                    + "\n- ".join(bad)
                    + "\n\n可把这些内容粘贴到输入框，或勾选“为附件新开会话”。"
                    "只用文字部分继续？")
                if ans != QMessageBox.StandardButton.Yes:
                    return
            text = with_doc_texts(text, doc_texts)

        how = ("沿用原会话追问（不新开会话）" if reuse
               else "新开会话并重新开始（上一轮上下文不可见）")
        extra = "（附 %d 个 PDF%s）" % (
            len(docs), "，已转为文字随消息发送" if reuse and docs else "") if docs else ""
        if QMessageBox.question(self, "重新提交分析",
                                "将%s：用原场景 + 补充资料重跑%s；原报告会保留。继续？"
                                % (how, extra)) != QMessageBox.StandardButton.Yes:
            return
        try:
            if reuse:
                ws = Path(parent_ws) if parent_ws and Path(parent_ws).is_dir() else None
                if ws is None:
                    ws = paths.workspace(paths.new_sid())
                names = [str(f) for f in ((parent.get("provenance") or {}).get("files") or [])]
            else:
                ws = paths.workspace(paths.new_sid())
                names = paths.stage_files(self.files, ws)
                attach_names = paths.stage_files(docs, ws) if docs else []
        except Exception as exc:  # noqa: BLE001
            self.error.setText("工作区失败：%s: %s" % (type(exc).__name__, exc))
            self.error.setVisible(True)
            return
        scenario = build_supplemented_scenario(parent, text, attachments=attach_names)
        if mode == "sciencepal_hcc" and not names and not scenario.get("paper_text"):
            QMessageBox.warning(self, "缺少论文", "补充重跑仍需要论文 PDF 或正文。")
            return
        record_text = text or ("附件：" + "、".join(attach_names or [f["name"] for f in self.supplement_files]))
        sup = Supplement.from_visit(parent, record_text)
        for i, it in enumerate(sup.expected_items):
            if i < len(self.supplement_inputs):
                _, edit = self.supplement_inputs[i]
                it.value = edit.toPlainText().strip()
                it.checked = bool(it.value)
        try:
            supplement_store.save(sup)
        except Exception as exc:  # noqa: BLE001
            self.error.setText("补充记录保存失败：%s: %s" % (type(exc).__name__, exc))
            self.error.setVisible(True)
            return
        self._supplement_parent = parent
        self._supplement_record = sup
        self.workspace = ws
        self.workspace_label.setText("工作区：%s" % ws)
        self.workspace_label.setToolTip(str(ws))
        self.open_ws_btn.setEnabled(True)
        self.error.setVisible(False)
        self.progress.setVisible(True)
        self.start_btn.setEnabled(False)
        self._rebuild_stages()
        self.run_started = time.time()
        self.timer.start()
        self.statusBar().showMessage("补充资料重跑中…")
        self.tabs.setCurrentIndex(0)
        self._validate_supplement()
        self._launch_worker(mode, scenario, names, ws, attachments=attach_names,
                            thread_id=tid if reuse else None, resume=reuse)

    def _ensure_stage_row(self, key: str, label: str | None = None):
        """找到 stage 行；逐篇事件（如 match#0 / hcc#0）按父行之后插入。"""
        for i in range(self.stage_list.count()):
            item = self.stage_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == key:
                return item
        text = "%s　·　待开始" % (label or key)
        item = QListWidgetItem(text)
        item.setData(Qt.ItemDataRole.UserRole, key)
        item.setData(Qt.ItemDataRole.UserRole + 1, label or key)
        insert_at = -1
        if "#" in key:
            parent = key.split("#")[0]
            for i in range(self.stage_list.count()):
                k = str(self.stage_list.item(i).data(Qt.ItemDataRole.UserRole) or "")
                if k == parent or k.startswith(parent + "#"):
                    insert_at = i + 1
        if insert_at >= 0:
            self.stage_list.insertItem(insert_at, item)
        else:
            self.stage_list.addItem(item)
        return item

    def _on_stage(self, ev: dict):
        key = ev.get("stage")
        if not key:
            return
        item = self._ensure_stage_row(key, ev.get("label"))
        label = item.data(Qt.ItemDataRole.UserRole + 1) or key
        status = ev.get("status")
        if status == "start":
            item.setText("%s　·　运行中…" % label)
            item.setForeground(QBrush(QColor("#17457f")))
        elif status == "ok":
            extra = []
            if ev.get("ms"):
                extra.append("%dms" % ev["ms"])
            if ev.get("detail"):
                extra.append(str(ev["detail"]))
            item.setText("%s　·　完成%s" % (label, ("　·　" + "　".join(extra)) if extra else ""))
            item.setForeground(QBrush(QColor("#1e8449")))
        elif status == "error":
            item.setText("%s　·　失败：%s" % (label, ev.get("detail") or ""))
            item.setForeground(QBrush(QColor("#c0392b")))
        elif status == "degraded":
            item.setText("%s　·　降级：%s" % (label, ev.get("detail") or ""))
            item.setForeground(QBrush(QColor("#b9770e")))
        elif status == "pending":
            item.setText("%s　·　等待人工：%s" % (label, ev.get("detail") or ""))
            item.setForeground(QBrush(QColor("#b9770e")))

    def _on_done(self, visit: dict):
        self.timer.stop()
        parent = self._supplement_parent
        rec = self._supplement_record
        if parent:
            self.previous_visit = parent
            if rec:
                rec.applied = True
                rec.new_visit_id = visit.get("visit_id")
                try:
                    supplement_store.save(rec)
                except Exception:
                    pass
            self._set_report_switch(visit, parent)
        else:
            self.previous_visit = None
            self.report_switch.setVisible(False)
        self._supplement_parent = None
        self._supplement_record = None
        self.visit = visit
        self.progress.setVisible(False)
        self.start_btn.setEnabled(True)
        self.pdf_btn.setEnabled(True)
        self.json_btn.setEnabled(True)
        elapsed = int(time.time() - self.run_started) if self.run_started else 0
        self.statusBar().showMessage("完成 · 用时 %ds · %s" % (elapsed, visit.get("visit_id", "")))
        self._render(visit)
        self.runFinished.emit()

    def _on_failed(self, msg: str):
        self.timer.stop()
        if self._supplement_record:
            self._supplement_record.applied = False
            try:
                supplement_store.save(self._supplement_record)
            except Exception:
                pass
            self._supplement_parent = None
            self._supplement_record = None
            self.supplement_status.setText("补充重跑失败；原报告仍保留，可修改补充内容再试。")
        self.progress.setVisible(False)
        self.start_btn.setEnabled(True)
        self.error.setText("失败：%s" % msg)
        self.error.setVisible(True)
        elapsed = int(time.time() - self.run_started) if self.run_started else 0
        self.statusBar().showMessage("失败 · 用时 %ds" % elapsed)
        self.runFinished.emit()

    def _thread_finished(self):
        if self.thread is not None:
            self.thread.deleteLater()
        self.thread = None
        self.worker = None
        self._validate_supplement()

    # ---------------- 渲染 ----------------
    def _apply_raw_visibility(self, checked=None):
        """原始返回页：默认不显示；勾选后插到「补充资料」之后（即最后一个页签）。"""
        if checked is None:
            checked = self.show_raw.isChecked()
        idx = self.tabs.indexOf(self.raw_page)
        if checked and idx < 0:
            sup = self.tabs.indexOf(self.supplement_page)
            at = (sup + 1) if sup >= 0 else self.tabs.count()
            self.tabs.insertTab(min(at, self.tabs.count()), self.raw_page, "原始返回")
        elif not checked and idx >= 0:
            self.tabs.removeTab(idx)

    def _render(self, v: dict):
        domain = v.get("domain") or {}
        dtext = {
            "in_scope": "域内：肝癌治疗/诊断相关",
            "outside_scope": "域外：不属于肝癌治疗/诊断",
            "unclear": "无法确定是否在域内",
        }.get(domain.get("status"), "未知")
        dcolor = {"in_scope": "#1e8449", "outside_scope": "#c0392b", "unclear": "#b9770e"}.get(
            domain.get("status"), "#333")
        self.domain.setTextFormat(Qt.TextFormat.RichText)
        self.domain.setText("<b style='color:%s'>%s</b> — %s" % (
            dcolor, escape(dtext), escape(str(domain.get("note_zh") or ""))))

        fallback = v.get("parse_mode") == "text_fallback"
        self.fallback.setVisible(fallback)
        if fallback:
            self.fallback.setText("Agent 未按 JSON 返回，以下为原文摘要；结论需人工判读。")

        repaired = any(a.get("stage") == "hcc_original" for a in (v.get("raw_answers") or []))
        self.repair_note.setVisible(repaired)
        if repaired:
            self.repair_note.setText("二次 JSON 修复已生效：首次返回的 Markdown 已保留在「原始返回」"
                                     "（已自动显示；平时默认隐藏）。")
        if (fallback or repaired) and not self.show_raw.isChecked():
            self.show_raw.setChecked(True)      # 降级/修复时自动展示，便于人工判读
        else:
            self._apply_raw_visibility()

        summary = str((v.get("integrated") or {}).get("summary_zh") or "")
        if summary.strip():
            self.summary.setMarkdown(summary)
        else:
            self.summary.setPlainText("")

        cautions = (v.get("integrated") or {}).get("cautions_zh") or []
        self.cautions.setHtml("<ul>%s</ul>" % "".join("<li>%s</li>" % escape(str(c)) for c in cautions))

        self._render_matrix(v)
        self._render_misc(v)

        raw_parts = []
        for a in v.get("raw_answers") or []:
            raw_parts.append("===== %s =====\n%s" % (a.get("stage"), a.get("text")))
        self.raw.setPlainText("\n\n".join(raw_parts) or "（无原始返回）")

        mode = v.get("mode")
        self.badge.setText({
            "stub": "离线自检（非真实结论）",
            "sciencepal": "SciencePal Agent",
            "sciencepal_hcc": "SciencePal · HCC-Fit 报告",
        }.get(mode, mode or ""))
        if self.supplement_files:            # 新一轮报告：上一轮的附件不再沿用
            self.clear_supplement_files()
        self._render_supplement(v)

    def _render_matrix(self, v: dict):
        papers = {p.get("paper_id"): p for p in (v.get("papers") or [])}
        matched = v.get("matched") or []
        self.matrix.setRowCount(len(matched))
        details = []
        for row, m in enumerate(matched):
            p = papers.get(m.get("paper_id")) or {}
            cells = [
                str(p.get("title") or m.get("paper_id")),
                TIER_TEXT.get(m.get("tier"), m.get("tier") or ""),
                STRENGTH_TEXT.get(m.get("evidence_strength"), m.get("evidence_strength") or "—"),
            ]
            dims = {d.get("id"): d for d in (m.get("dimensions") or [])}
            for key in ("population", "timepoint", "input", "outcome"):
                d = dims.get(key) or {}
                cells.append(STATUS_TEXT.get(d.get("status"), d.get("status") or "—"))
            for col, text in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setToolTip(text)
                if col >= 3:
                    status = (dims.get(("population", "timepoint", "input", "outcome")[col - 3]) or {}).get("status")
                    fg, bg = STATUS_COLOR.get(status, ("#333", "#f5f5f5"))
                    item.setForeground(QBrush(QColor(fg)))
                    item.setBackground(QBrush(QColor(bg)))
                self.matrix.setItem(row, col, item)
            details.append("<h3>%s · %s · 证据%s</h3>" % (
                escape(str(p.get("title") or m.get("paper_id"))),
                escape(TIER_TEXT.get(m.get("tier"), m.get("tier") or "")),
                escape(STRENGTH_TEXT.get(m.get("evidence_strength"), m.get("evidence_strength") or "—"))))
            for key in ("population", "timepoint", "input", "outcome"):
                d = dims.get(key) or {}
                details.append("<p><b>%s：</b>%s<br>%s</p>" % (
                    DIM_TEXT[key], escape(STATUS_TEXT.get(d.get("status"), d.get("status") or "—")),
                    escape(str(d.get("reason_zh") or ""))))
                if d.get("quote"):
                    details.append("<p style='color:#4a5a72'><i>“%s”</i> <span style='color:#8a97a6'>（%s）</span></p>" % (
                        escape(str(d["quote"])), escape(str(d.get("location") or "未标注"))))
        self.detail.setHtml("".join(details) or "<p style='color:#66727f'>没有结构化匹配结果（可能是纯文本降级）。</p>")
        self.matrix.resizeRowsToContents()

    def _render_misc(self, v: dict):
        parts = ["<h3>论文</h3>"]
        for p in v.get("papers") or []:
            parts.append("<p><b>%s</b>　%s　%s<br><span style='color:#66727f'>%s</span></p>" % (
                escape(str(p.get("title") or "")),
                escape(str(p.get("year") or "—")),
                "纳入" if p.get("decision") == "include" else "排除",
                escape(str(p.get("reason_zh") or ""))))
        opts = (v.get("integrated") or {}).get("options") or []
        parts.append("<h3>方案（不构成治疗建议）</h3>")
        parts.extend("<p><b>%s</b><br>%s</p>" % (escape(str(o.get("name") or "")),
                                                 escape(str(o.get("note_zh") or ""))) for o in opts)
        expected = v.get("expected_data") or []
        parts.append("<h3>还缺什么数据</h3>")
        parts.extend("<p><b>%s</b><br>%s%s</p>" % (
            escape(str(e.get("item_zh") or "")), escape(str(e.get("why_zh") or "")),
            ("<br>补齐可解锁：" + escape(str(e.get("unlocks_zh")))) if e.get("unlocks_zh") else "") for e in expected)
        uncertain = v.get("uncertainties") or []
        parts.append("<h3>我们不确定的事</h3><ul>")
        parts.extend("<li>%s</li>" % escape(str(u)) for u in uncertain)
        parts.append("</ul>")
        self.misc.setHtml("".join(parts))

    def _tick(self):
        if not self.run_started:
            return
        self.statusBar().showMessage("运行中… 已用 %ds" % int(time.time() - self.run_started))

    def open_workspace(self):
        if not self.workspace:
            QMessageBox.information(
                self, "还没有工作区",
                "先跑一次核查（或补充资料重跑），“工作区”才会生成，"
                "里面是 logs/、runs/、files/、sp_raw/。")
            return
        ok, how = links.open_directory(self.workspace)
        if ok:
            self.statusBar().showMessage("已打开工作区（%s）：%s" % (how, self.workspace), 8000)
            return
        QGuiApplication.clipboard().setText(str(self.workspace))
        QMessageBox.warning(
            self, "打开工作区失败",
            "没能调起资源管理器：%s\n\n"
            "路径已复制到剪贴板，可直接粘贴到资源管理器地址栏：\n%s" % (how, self.workspace))

    # ---------------- 测试连接 ----------------
    def _check_key(self, *_args):
        """SciencePal Key 的前置检查：空/非 ASCII/空格 → 红色警告；偏短/前缀可疑 → 黄色。"""
        from app.keycheck import check_key
        try:
            level, msg = check_key(self.key.text())
        except AttributeError:
            return "ok", ""
        colors = {"ok": "#1e8449", "warn": "#8a5a00", "error": "#c0392b"}
        self.key_warn.setStyleSheet("color:%s;font-size:9pt;" % colors[level])
        self.key_warn.setText("" if level == "ok" else msg)
        self.key_warn.setVisible(level != "ok")
        return level, msg

    def probe(self):
        if self.probe_thread is not None:
            return
        self.probe_btn.setEnabled(False)
        self.probe_msg.setText("测试中…")
        ws = paths.workspace("probe")
        self.probe_thread = QThread(self)
        self.probe_worker = ProbeWorker(str(ws), self.key.text().strip(), "", "")
        self.probe_worker.moveToThread(self.probe_thread)
        self.probe_thread.started.connect(self.probe_worker.run)
        self.probe_worker.result.connect(self._on_probe)
        self.probe_worker.result.connect(self.probe_thread.quit)
        self.probe_thread.finished.connect(self._probe_finished)
        self.probe_thread.start()

    def _on_probe(self, ok: bool, msg: str, names: list):
        text = ("✅ " if ok else "⚠️ ") + msg
        if not ok and names:                 # 成功时不再追加“可见：...”等尾巴
            text += "（可见：%s）" % "、".join(names)
        self.probe_msg.setText(text)
        self.probe_btn.setEnabled(True)

    def _probe_finished(self):
        if self.probe_thread is not None:
            self.probe_thread.deleteLater()
        self.probe_thread = None
        self.probe_worker = None

    # ---------------- 导出 ----------------
    def _default_path(self, suffix):
        docs = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.DocumentsLocation)
        base = Path(docs) if docs else Path.home()
        vid = (self.visit or {}).get("visit_id") or "visit"
        return str(base / ("HCC-Fit_%s.%s" % (vid, suffix)))

    def export_json(self):
        if not self.visit:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 JSON（给 Agent）",
                                              self._default_path("json"), "JSON (*.json)")
        if not path:
            return
        try:
            export.export_json(self.visit, path)
            self.statusBar().showMessage("已导出 JSON：%s" % path, 8000)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "导出失败", "%s: %s" % (type(exc).__name__, exc))

    def export_pdf(self):
        if not self.visit:
            return
        path, _ = QFileDialog.getSaveFileName(self, "导出 PDF（给人看）",
                                              self._default_path("pdf"), "PDF (*.pdf)")
        if not path:
            return
        try:
            export.export_pdf(self.visit, path)
            self.statusBar().showMessage("已导出 PDF：%s" % path, 8000)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "导出失败", "%s: %s" % (type(exc).__name__, exc))

    # ---------------- 设置 ----------------
    def _load_settings(self):
        s = QSettings("HCC-Fit", "Desktop")
        mode = s.value("mode", "sciencepal")
        idx = self.mode.findData(mode)
        if idx >= 0:
            self.mode.setCurrentIndex(idx)
        self.patient.setPlainText(s.value("patient", ""))
        self.task.setPlainText(s.value("task", ""))
        self.outcome.setText(s.value("outcome", "1 年内复发"))
        self.given.setText(s.value("given", ""))
        self.missing.setText(s.value("missing", ""))
        self.split_papers.setChecked(s.value("split_papers", "false") in (True, "true", "1", 1))
        self.show_raw.setChecked(s.value("show_raw", "false") in (True, "true", "1", 1))
        self._apply_raw_visibility()
        tp = s.value("timepoint", "pre_surgery")
        ti = self.timepoint.findData(tp)
        if ti >= 0:
            self.timepoint.setCurrentIndex(ti)

    def _save_settings(self):
        s = QSettings("HCC-Fit", "Desktop")
        s.setValue("mode", self.current_mode())
        s.setValue("patient", self.patient.toPlainText())
        s.setValue("task", self.task.toPlainText())
        s.setValue("outcome", self.outcome.text())
        s.setValue("given", self.given.text())
        s.setValue("missing", self.missing.text())
        s.setValue("timepoint", self.timepoint.currentData())
        s.setValue("split_papers", self.split_papers.isChecked())
        s.setValue("show_raw", self.show_raw.isChecked())
        # 不保存 Key

    def closeEvent(self, event):
        if self.thread is not None:
            QMessageBox.information(self, "正在运行", "当前任务还没结束，请等待完成后再关闭。")
            event.ignore()
            return
        if self.probe_thread is not None:
            QMessageBox.information(self, "正在测试", "测试连接还没结束，请稍等。")
            event.ignore()
            return
        self.timer.stop()
        self._save_settings()
        event.accept()
