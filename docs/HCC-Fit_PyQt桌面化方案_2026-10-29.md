# HCC-Fit 桌面化方案：Web → PySide6 本地软件（2026-10-29）

> 结论：**可行，而且改动比想象中小**。现有业务逻辑（五阶段/单次核查、SciencePal 适配、契约归一、报告映射）与 HTTP/前端是分开的，可以直接复用；需要重写的是 `server.py` 的传输层和 `static/` 的界面层。
>
> 推荐：**PySide6（LGPL）+ QThread + 原生控件**，保留 Python 3.12、纯标准库后端（除 PySide6 外不加依赖）。

---

## 0. 已确认决策（2026-10-29）

| # | 决策 | 影响 |
|---|---|---|
| 1 | 用 **PySide6**，不用 PyQt6 | LGPL，闭源分发友好；API 与 PyQt6 基本一致，本文代码示例把 `PyQt6` 换成 `PySide6` 即可 |
| 2 | **桌面版完全替代 Web 版** | `server.py` 的 HTTP/SSE 退役，`static/` 不再维护；业务 core 单份维护 |
| 3 | **Key 每次输入**，不保存 | 只放 QLineEdit 内存；QSettings 不写 Key；关闭窗口即丢 |
| 4 | **两个导出按钮**：PDF 给人、JSON 给 Agent | 可行，详见 §6.5；都从同一份 `visit.json` 生成 |
| 5 | **先做 Windows**，成熟后再适配 macOS/Linux | 代码避免 Windows-only API；工作区用 `QStandardPaths`，字体做 fallback；打包按平台分别做 |

> 说明：原文标题写的是 PyQt，实现统一用 **PySide6**；两者控件/信号写法几乎一致，差异主要在 import 和少量枚举。

> **P0–P4（Windows）已落地（2026-10-29）**：桌面版在 `hccfit_desktop/`，三栏窗口、5 种模式、真实 SciencePal 单次核查（含二次 JSON 修复）、四维/证据强度/引文/注意点/原始返回、人工工作区入口、运行计时、PDF（含 Markdown 结论）/JSON 导出、offscreen 冒烟测试均通过；PyInstaller one-dir 已产出 `dist/HCC-Fit/HCC-Fit.exe`（约 114MB），`--smoke` 退出码 0。下一步：Windows 安装包/签名（可选）→ macOS/Linux 适配。
>
> **2026-10-30 更新**：大脑模式从 5 种减到 **2 种**（SciencePal 五阶段 / HCC 单次），“演示(stub) / 本地模拟(mock_sp) / 人工中转(manual)”按用户要求从界面移除（stub 仅留给自动化测试）；Router 改为**内置进程内引擎**（`app/router_engine.py`），一键检索、无需启动服务。下文涉及这 3 种模式与“单独起 Router”的描述已作废，保留作历史记录。

---

## 1. 哪些能直接复用

| 现有文件 | 作用 | 桌面版处理 |
|---|---|---|
| `planner.py`（`Planner` / `HccPlanner`） | 五阶段 / 单次核查编排，`emit` 回调 | **原样复用**，`emit` 直接接到 Qt 信号 |
| `brains.py`（Stub/Manual） | 演示、人工中转 | 原样复用 |
| `sciencepal.py` | SciencePal 适配（initiate/poll/stream/追问） | 原样复用；网络调用放 QThread |
| `contract.py` | 宽进严出归一 | 原样复用 |
| `hcc_report.py` | 单次报告提示词 + 映射 | 原样复用 |
| `pdftext.py` | PDF 抠字兜底 | 原样复用 |
| `schemas/visit.schema.json` + `tools/validate_output.py` | 产物校验 | 原样复用（打包进资源） |
| `tools/mock_sciencepal.py` | 本地假平台 | 保留；`mock_sp` 模式在后台线程起本地假服务 |
| `selftest.py` / `test_*.py` | 自测 | 后端测试保留；新增 UI 冒烟测试 |
| `server.py` | HTTP、SSE、上传、`/api/*` | **大部分退役**；只保留 `run_pipeline` 里的“选 brain/planner”逻辑，抽到 `runner.py` |
| `static/` | HTML/JS/CSS | **全部替换**为 Qt 控件 |
| `start.bat` | 起服务 + 开浏览器 | 换成 `python main.py` / 打包后的 `.exe` |

**关键点**：`Planner` / `HccPlanner` 已经是 `emit(event)` 回调，天然适配 Qt 的 `pyqtSignal`；不需要把业务逻辑重写成 Qt 风格。

---

## 2. 目标架构

```
┌─────────────────────────────────────────────────────┐
│ main.py  QApplication                               │
│  └─ MainWindow                                      │
│      ├─ 左：场景 / 文件 / 大脑（QScrollArea）        │
│      ├─ 中：流程（QListWidget + QProgressBar）       │
│      └─ 右：结果（QTabWidget / QTableWidget）        │
│                                                     │
│  Controller（主线程）                                │
│      ├─ RunWorker(QObject) ── QThread ──┐            │
│      │     stage / done / failed 信号    │            │
│      └─ ProbeWorker(QObject) ── QThread ─┘            │
│                                                     │
│  core（原样复用）                                    │
│    runner.build_planner() → Planner / HccPlanner    │
│    brains / sciencepal / contract / hcc_report      │
│    workspace.py（文件落盘、sid、路径）               │
└─────────────────────────────────────────────────────┘
```

**不再需要**：HTTP server、SSE、CORS、`/api/upload`、`/api/run`、`fetch`、EventSource。
**仍然需要**：HTTPS 出站（SciencePal）、本地文件读写、`workspaces/` 落盘。

---

## 3. 建议目录结构

```
hccfit_desktop/
├── main.py                 # QApplication + MainWindow
├── ui/
│   ├── main_window.py      # 主窗口、菜单、状态栏
│   ├── scenario_panel.py   # 场景表单
│   ├── file_panel.py       # 拖拽/选择文件
│   ├── brain_panel.py      # 模式/Key/Base/Agent/测试连接
│   ├── stage_panel.py      # 流程列表 + 进度
│   ├── result_view.py      # 结论/四维/论文/期望/不确定/原始
│   ├── workers.py          # RunWorker / ProbeWorker / FileWorker
│   └── styles.py           # 字体、颜色、QSS
├── core/                   # 从 HCCFIT_APP 平移，尽量不改
│   ├── planner.py
│   ├── brains.py
│   ├── sciencepal.py
│   ├── contract.py
│   ├── hcc_report.py
│   ├── pdftext.py
│   ├── runner.py           # 新增：mode → brain/planner 的分支
│   ├── workspace.py        # 新增：文件复制、sid、路径
│   └── validate_output.py
├── resources/
│   ├── schemas/visit.schema.json
│   └── icon.ico
├── tests/
│   ├── test_backend.py     # 复用现有 39 项
│   └── test_ui_smoke.py    # pytest-qt，offscreen
├── pyproject.toml
└── build.spec              # PyInstaller
```

> 如果不想大改 import：把 `core/` 的模块保持扁平（`from planner import ...`），只把 UI 放 `ui/`，也能跑；包化可以后做。

---

## 4. UI 映射（Web → Qt）

| Web 元素 | Qt 控件 | 说明 |
|---|---|---|
| 顶部标题 + 模式徽章 | `QToolBar` + `QLabel` | 模式徽章用 QSS 变色 |
| 场景 6 字段 | `QFormLayout` + `QLineEdit` / `QTextEdit` / `QComboBox` | 使用时点用 `QComboBox`（5 个枚举） |
| 论文关键段落 | `QPlainTextEdit` | 等宽字体，placeholder |
| 拖拽上传 | 自定义 `QListWidget`：`dragEnterEvent` / `dropEvent` | 支持多选；显示文件名 + KB |
| 选择文件 | `QFileDialog.getOpenFileNames` | 过滤 `*.pdf *.md *.txt` |
| 模式单选 | `QComboBox` 或 `QButtonGroup` + `QRadioButton` | 5 个模式 |
| Key / Base / Agent | `QLineEdit`（Key 用 `EchoMode.Password`） | Key 只在内存 |
| 测试连接 | `QPushButton` → `ProbeWorker` | 结果写 `QLabel`/`QMessageBox` |
| 开始按钮 | `QPushButton` | 运行中禁用 |
| 五阶段流程 | `QListWidget`（每行自定义 widget） | 状态色：灰/蓝/绿/红/黄 |
| 进度 | `QProgressBar`（indeterminate） | 运行中 `setRange(0,0)` |
| 就诊反馈 | `QTabWidget`：结论/四维/论文/方案/期望/不确定/原始 | 四维可用 `QTableWidget` 或自定义卡片 |
| 引文 | `QTextBrowser` 或 `QToolTip` | 显示 quote + location |
| 原始返回 | 只读 `QPlainTextEdit`（放在 tab/抽屉） | 不再需要 `<details>` |
| 下载 visit.json | `QFileDialog.getSaveFileName` + `json.dump` | 可选导出 PDF |
| 错误横幅 | `QMessageBox` + 状态栏/内嵌 `QLabel` | 网络错误给人话 |
| 底部 disclaimer | `QLabel`（wordWrap） | 常驻不可折叠 |

**布局建议**：`QSplitter` 三栏（左 380px / 中 280px / 右自适应），窗口最小 1200×800；高 DPI 由 Qt6 自动处理；中文字体 `Microsoft YaHei`。

---

## 5. 线程模型（关键）

所有网络/耗时操作必须离开 UI 线程，否则窗口会“未响应”。

```python
class RunWorker(QObject):
    stage = pyqtSignal(dict)
    done = pyqtSignal(dict)
    failed = pyqtSignal(str)

    def __init__(self, mode, workspace, scenario, files, opts):
        super().__init__()
        self.args = (mode, workspace, scenario, files, opts)

    @pyqtSlot()
    def run(self):
        try:
            planner = build_planner(*self.args, emit=self.stage.emit)
            self.done.emit(planner.run())
        except Exception as exc:
            self.failed.emit("%s: %s" % (type(exc).__name__, exc))
```

主窗口：

```python
self.thread = QThread(self)
self.worker = RunWorker(...)
self.worker.moveToThread(self.thread)
self.thread.started.connect(self.worker.run)
self.worker.stage.connect(self.on_stage)      # 队列连接，回 UI 线程
self.worker.done.connect(self.on_done)
self.worker.failed.connect(self.on_failed)
self.worker.done.connect(self.thread.quit)
self.worker.failed.connect(self.thread.quit)
self.thread.start()
```

- `build_planner()` 就是现在 `server.run_pipeline` 里那段 mode 分支，抽到 `core/runner.py`：
  - `stub/manual` → `Planner(build_brain(...))`
  - `mock_sp` → `SciencePalBrain(base=ensure_mock())` + `Planner`
  - `sciencepal` → `SciencePalBrain` + `Planner`
  - `sciencepal_hcc` → `SciencePalBrain(strict_agent=True)` + `HccPlanner`
- `probe` 同理：`ProbeWorker` 调 `SciencePalBrain.probe()`。
- 取消：`QThread.requestInterruption()` + 在 `SciencePalBrain` 的轮询循环里检查 `isInterruptionRequested()`；第一版可以只做“禁用按钮 + 等结束”。

---

## 6. 文件、工作区、Key

- **工作区**：默认 `%LOCALAPPDATA%\HCC-Fit\workspaces\<sid>`（避免 Program Files 只读），可在设置里改。
- **上传**：不再走 HTTP；`FilePanel` 拿到路径后 `workspace.add_files(paths)` 复制到 `workspace/files/`，文件名用现有 `safe_name` 清洗。
- **PDF 文本**：`pdftext.extract()` 可在后台 Worker 里预抽，成功就填到“论文关键段落”，失败给提示。
- **Key**：
  - 只放 `QLineEdit` 内存；关闭窗口即丢；
  - `QSettings` 只存 base/agent/mode/场景，不存 Key；
  - 如要“记住 Key”，用 Windows Credential Manager（`keyring`）或 DPAPI，不要明文写 ini；
  - 日志/`visit.json`/`sp_raw` 不写 Key（现有代码已经保证）。
- **结果落盘**：仍写 `workspaces/<sid>/runs/*.json`，保留“重启后按 visit 找回”的能力。

---

## 6.5 导出设计：PDF 给人，JSON 给 Agent

**完全可行**，两个按钮共用同一份 `visit` 数据，不需要再请求 Agent。

### JSON（给 Agent / 下游程序）

- 内容就是现有的 `visit.json`，**不额外包一层**，保持 schema 可直接校验：
  ```python
  with open(path, "w", encoding="utf-8") as fh:
      json.dump(visit, fh, ensure_ascii=False, indent=2)
  ```
- 文件名：`HCC-Fit_<visit_id>.json`；
- 包含 `raw_answers`（含 `hcc_original` 与修复后 JSON）、`provenance`、`parse_mode`；
- 不放 Key、不放 `sp_raw` 里的请求头；
- 按钮文案：**导出 JSON（给 Agent）**；导出前可选调用 `validate_output.validate(visit, schema)`，有错时提示但仍允许导出（或阻止，建议提示）。

### PDF（给人看）

用 **Qt 原生**生成，不引入 reportlab/weasyprint：

- `QTextDocument` 写 HTML（Qt 支持子集）+ `QPdfWriter` 输出 A4；
- 或用 `QPrinter` + `QPrintDialog` 提供“打印/另存 PDF”；
- 中文字体：从 `QFontDatabase` 里按 `Microsoft YaHei → PingFang SC → Noto Sans CJK SC` 回退；

**一页/两页版式**：

```
HCC-Fit 核查报告                        2026-10-29 14:xx
模式：SciencePal · HCC-Fit 报告         visit_id: visit_...
────────────────────────────────────────────
场景：人群 / 问题 / 时点 / 结局
判定：域内 / 域外 / 无法确定
综合结论：……
────────────────────────────────────────────
四维核查（表格，带颜色/符号）
人群  ✅ 匹配    理由……   引文：“……”（p.3）
时点  ❌ 不匹配  理由……   引文：“……”
输入  ⚠ 无法确认 理由……
结局  ✅ 匹配    ……
证据强度：中（内部验证；外部验证未报告）
────────────────────────────────────────────
还缺什么：……
我们不确定的事：……
注意点：……
────────────────────────────────────────────
免责声明（逐字）
页脚：HCC-Fit · 非诊断/非治疗建议 · 第 1/2 页
```

- `stub/mock_sp` 模式在页眉加明显水印：**“演示数据，不是真实论文结论”**；
- `parse_mode=text_fallback` 时：首页黄条 + “Agent 未按 JSON 返回，以下为原文”，正文放原文摘要；JSON 里仍保留完整 `raw_answers`；
- 预览：`QPrintPreviewDialog`（可选）；
- 文件名：`HCC-Fit_<visit_id>.pdf`；
- 不用把 `raw_answers` 全文塞进 PDF（给人看的是结论；原文放 JSON / 界面抽屉）。

### 实现位置

```
core/export.py
  def export_json(visit, path) -> None
  def build_report_html(visit) -> str
  def export_pdf(visit, path, font_family) -> None   # QTextDocument + QPdfWriter
ui/result_view.py
  btn_json.clicked -> QFileDialog.getSaveFileName(... ".json")
  btn_pdf.clicked  -> QFileDialog.getSaveFileName(... ".pdf")
```

> PDF 里的文字全部来自 `visit`，不重新调用 Agent；因此导出是**离线、即时、可重复**的。

---

## 7. 分阶段实施

| 阶段 | 内容 | 产出 | 预估 |
|---|---|---|---|
| P0 | 骨架：`main.py` + 三栏窗口 + 场景表单 + 流程列表；stub 模式跑通 | 能点“开始”，看到五阶段与结果 | 0.5 天 |
| P1 | `RunWorker` + 真实模式（sciencepal / sciencepal_hcc / mock_sp）+ Key/Probe | 单篇/五阶段都能跑 | 1 天 |
| P2 | 结果视图：四维表、引文、注意点、原始返回、导出 JSON | 和 Web 版功能对齐 | 1 天 |
| P3 | 文件拖拽、工作区、manual 模式、错误 UX、设置 | 日常可用 | 0.5–1 天 |
| P4 | PyInstaller 打包、图标、干净机器验证、pytest-qt | 可分发 `.exe` | 0.5–1 天 |

合计 **3–5 天**（不含临床内容评审）。

---

## 8. 测试

- **后端**：现有 39 项全保留（`selftest` 里依赖 HTTP 的部分改成直接调 `runner`，或保留一个测试专用的 HTTP 入口）。
- **UI 冒烟**（`pytest-qt`）：
  ```python
  os.environ["QT_QPA_PLATFORM"] = "offscreen"
  def test_stub_run(qtbot):
      win = MainWindow()
      qtbot.addWidget(win)
      win.mode.setCurrentText("stub")
      win.start_button.click()
      qtbot.waitUntil(lambda: win.result_view.row_count() > 0, timeout=10000)
  ```
- **打包验证**：干净 Windows 10/11 虚拟机，无 Python，双击 exe，stub 模式跑通；再用真 Key 跑一次。
- **回归**：`python tools/test_hcc_report.py` 等仍可在源码目录跑。

---

## 9. 打包与分发

```bash
pip install PyQt6 pyinstaller
pyinstaller --windowed --name HCC-Fit \
  --icon resources/icon.ico \
  --add-data "resources/schemas;schemas" \
  main.py
```

- 建议 **one-dir**（启动快、杀软误报少）；one-file 体积更小但启动慢。
- 体积约 60–100MB（Qt 运行时）。
- 代码签名可选；无签名时 SmartScreen 会提示。
- **许可证**：PyQt6 是 GPL/商业双许可；如果软件要闭源分发，改用 **PySide6（LGPL）**，API 几乎一样，迁移成本很低。

---

## 9.5 跨平台预留（先 Windows，后 macOS/Linux）

Windows 先做，但代码现在就避开平台绑定，后面不用重写：

| 项 | Windows 现在 | 跨平台做法 |
|---|---|---|
| 工作区路径 | `QStandardPaths.AppLocalDataLocation` | 同一 API，自动落到 `~/.local/share` / `~/Library/Application Support` |
| 设置 | `QSettings("HCC-Fit", "Desktop")` | 同一 API（注册表 / plist / ini） |
| 打开文件夹 | `QDesktopServices.openUrl(QUrl.fromLocalFile(...))` | 同一 API |
| 字体 | `Microsoft YaHei` | `QFontDatabase` 回退：`PingFang SC` / `Noto Sans CJK SC` |
| 路径 | `pathlib.Path`，不拼反斜杠 | 天然跨平台 |
| PDF | `QPdfWriter` + 系统 CJK 字体 | 同名 API；Linux 需确认 Noto CJK 已安装 |
| 打包 | PyInstaller one-dir + 图标 | macOS：`.app` + 签名/公证；Linux：AppImage |
| 平台判断 | `sys.platform` | 只用于打包/字体，不用于业务逻辑 |

**提醒机制**：Windows 版验收通过后，在 `README`/发布清单里加一条 “Windows 已验证；macOS/Linux 待适配（字体、打包、签名）”，并给你一个 checklist，成熟后按清单逐项做，不需要改 core。

---

## 10. 风险与对策

| 风险 | 对策 |
|---|---|
| UI 卡死 | 所有网络/PDF 操作放 QThread；按钮禁用 + 进度条 |
| SciencePal 单次核查 2–4 分钟 | 进度条 indeterminate + 状态栏计时；支持取消（轮询里检查） |
| Key 泄露 | 内存 only；不写 QSettings/日志/visit；可选 Credential Manager |
| `mock_sp` 起本地端口 | 后台线程启动；端口冲突时换随机端口；或改成纯内存假 brain |
| `manual` 模式没有“人”的入口 | 加“打开人工工作区”按钮（`QDesktopServices.openUrl`） |
| PyQt GPL | 闭源分发改 PySide6；或买商业许可 |
| 打包后 schema 找不到 | `sys._MEIPASS` 资源路径封装 `resource_path()` |
| 中文/高 DPI | Qt6 默认高 DPI；字体 `Microsoft YaHei`；避免固定像素 |
| 旧 Web 版维护 | 保留 `HCCFIT_APP` 作为参考；桌面版复用 core，不双维护 UI |

---

## 11. 可选方案对比

| 方案 | 工作量 | 体积 | 体验 | 建议 |
|---|---|---|---|---|
| **PyQt6/PySide6 原生** | 3–5 天 | 60–100MB | 原生、稳定 | ✅ 推荐 |
| PyQt6 + `QWebEngineView` 嵌现有 HTML | 0.5–1 天 | 150MB+ | 仍是网页，需保留 server 或 JS bridge | 只适合临时过渡 |
| pywebview 套壳 | 0.5 天 | 小 | 网页感 | 不符合“PyQt 原生”目标 |
| Tkinter | 3 天 | 小 | 控件弱、表格/富文本差 | 不推荐 |

---

## 12. 验收标准

- [ ] 不启动任何 HTTP 服务，双击 exe 即可用；
- [ ] 5 种模式都能跑（stub / sciencepal / sciencepal_hcc / mock_sp / manual）；
- [ ] 场景、文件、Key、Agent、Base 与 Web 版一致；
- [ ] 五阶段/单次核查的进度、耗时、失败原因可见；
- [ ] 四维矩阵、引文、注意点、期望数据、原始返回都能看；
- [ ] Key 不出现在设置文件、日志、`visit.json`；
- [ ] 后端 39 项测试仍全绿，UI 冒烟通过；
- [ ] 干净 Windows 机器上可运行。

---

## 13. 需要确认

1. **PyQt6 还是 PySide6**？（闭源分发建议 PySide6，API 基本一致）
2. 是否需要**保留 Web 版**（同一套 core 双前端），还是桌面版完全替代？
3. Key 是否要“记住”（Credential Manager），还是每次输入？
4. 是否需要导出 PDF 报告（`QPrinter`/`QTextDocument`），还是只导 JSON？
5. 目标系统：Windows 10/11 单机，还是也要 macOS/Linux？

确认后可以按 P0 先搭骨架，1 天内能看到 stub 模式的桌面窗口。
