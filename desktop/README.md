# HCC-Fit 桌面版（PySide6）

> P0–P4 + 输入层 P1 已完成：单输入场景卡、三栏窗口、5 种模式、真实 SciencePal 单次核查、四维/证据/原始返回、PDF/JSON 导出、人工工作区入口、Windows one-dir。
> Web 版（`HCCFIT_APP`）保留作对照；桌面版复用它的 core，不再起 HTTP 服务。

## 运行

```bash
pip install PySide6
python main.py
```

或双击 `run_desktop.bat`。

## 当前功能（P0–P4 + 输入层）

- 场景：**一个自由输入框**（一段话）+ 解析后的可编辑场景卡；追问、确认后才进入核查；
- 论文：拖拽或选择文件、粘贴关键段落；
- 大脑：5 种模式（stub / sciencepal / sciencepal_hcc / mock_sp / manual）、Key、Base、Agent、测试连接；
- 流程：五阶段或 HCC 单次核查的实时状态；
- 结果：结论、四维表（含证据强度）、逐维理由与引文、论文/方案/期望、原始返回；
- 二次 JSON 修复：首次返回 Markdown 时自动追问 JSON，成功后绿色提示，原始 Markdown 保留；
- 运行计时、当前阶段、失败原因；“打开工作区”可看 `run.log` / `sp_raw/` / 人工文件；
- 导出：PDF（给人看，一页/两页 A4）与 JSON（`visit.json`，给 Agent）。

## 输入归一（P1 已完成；2026-10-30 补齐 P0 三项）

- 场景只有一个输入框：医生写一段话，不要求标准词；
- 解析后弹“场景卡”：字段 / 原话 / 整理值 / 状态 / 来源片段 / 确认 / **信息是否足够**，可直接改；
- 集中追问（疾病类型、治疗、起点、结局等），允许“不知道”；**原话已有的信息不再追问**（见下）；
- 未确认、有冲突、超出 HCC 范围时，“进入核查”禁用；
- 修改后重新确认，`input_session.version + 1`；
- `肝癌、肝脏肿瘤、恶性肿瘤` 不会被自动归为 HCC；
- 每字段保存原话、整理值、状态、来源片段、确认状态、`concept_id`；
- **不再有 AI 候选层**（2026-10-30 按用户要求移除 `app/ai_extract.py` 及界面入口）；
- Agent v0.6 不修改，确认后的场景卡映射为原 `scenario`；
- 输入会话存 `AppLocalData/inputs/<input_id>.json`，原话不覆盖。

### 追问抑制（sufficient）

只有“信息不足”的字段才生成追问；判定写死在 `field_sufficient()`，可在场景卡“信息”列看到每字段是否足够：

| 字段 | 算“足够”的条件 |
|---|---|
| 疾病 | 明确 HCC（“肝癌/肝脏肿瘤/恶性肿瘤”不算） |
| 治疗 | 明确切除 / TACE / 消融 / 系统治疗 / 肝移植；“刚切完”只确认大类，不问术式 |
| 结局 | 明确 OS 或复发（RFS）；只说“生存/预后”仍要问 |
| 起点 | 明确手术日 / 治疗日 / 今天 |
| 问题 | 原话已经写清楚想问什么 |

### 空格软切分

医生常不写标点，只用空格断句。`split_sentences()` 现在 **硬标点 + 空格软切分**，并保护这些短语不被拆开：

- 数字 + 单位：`3 年`、`58 岁`、`1.5 cm`、`30 %`；
- 否定 + 内容：`非 HCC`、`无 腹水`、`未见 复发`；
- 拉丁复合名：`Child-Pugh A`、`hepatocellular carcinoma`、`BCLC B`、`ECOG 0`；
- 多个空格 / 全角空格 / 制表符视为明确分隔，照切。

因此 `58 岁男性 刚切完 想看看三年生存` 与 `HCC 准备肝切除 从手术日 想看看 3 年 OS` 都能正确解析（后者 `3 年` → 36 个月）。

![场景卡：信息列与追问抑制](docs/input_review.png)

命令行演示 / 测试：

```bash
python tools/input_demo.py "肝癌患者，刚切完，病理还没出来，想看看三年生存。"
python tests/test_input_normalization.py   # 15/15
python tests/test_input_review.py          # 场景卡 UI 冒烟（含零追问场景）
```

## 报告后补充资料

报告给出 `expected_data` 后，结果页有“补充资料”页签：

- 上方用**一个只读文本框**显示报告认为还缺的资料，自动换行、不横向滚动；
- 下方**每一条缺失资料对应一个输入框**，填这一项的资料；
- “重新提交分析”：用**原场景 + 补充原文**全量重跑，不改 Agent v0.6；
- 原报告保留，完成后顶部可切换“本次报告 / 上一次报告”；
- 补充记录存 `AppLocalData/supplements/<supplement_id>.json`；
- 补充只追加资料，不自动改诊断；改变疾病/治疗/时点请回场景卡确认；
- 补充原文会按“资料名：内容”拼成一段，再交给下一次全量重跑。

测试：

```bash
python tests/test_supplement.py      # 拼接/落盘/空输入
python tests/test_supplement_ui.py   # stub → 补充 → 重跑 → 新旧切换
```

![补充资料页签](docs/supplement_tab.png)

## 可选：PubMed 候选（Router 方案 A）

论文区新增 **“先找候选论文（PubMed）”**：

- 需要先单独启动 `HCC-Fit_Simple_Router`（默认 `http://127.0.0.1:8080`）；
- Router 服务端仍需配置 `SCIENCEPAL_FILTER_AGENT_ID`，否则 `balanced` 会降级为 fast；
- 打开对话框，选 `fast`（5–10 秒）或 `balanced`（60–90 秒），选 k（1–8）；
- 可填 Router 的 `HCCFIT_ROUTER_TOKEN`（服务鉴权，可选）；
- `balanced` 还可以在弹窗里填 **SciencePal Key**（只放内存，关闭窗口清空）；不填则用 Router 服务端的 `SCIENCEPAL_API_KEY`；
- 勾选候选后，保存为 `router_candidates_*.md` 并加入论文列表；
- **这只是题录/摘要，不是全文**；S2 四维核查仍需 PDF 或正文；
- 该步骤是**可选前置**，不替换 S1，五阶段 Agent 逻辑不变。

环境变量：

```bash
set HCCFIT_ROUTER_URL=http://127.0.0.1:8080
```

测试：

```bash
python tests/test_router_client.py
```

## 数据与密钥

- 工作区：`QStandardPaths.AppLocalDataLocation/workspaces/<sid>`；
- 输入会话：`QStandardPaths.AppLocalDataLocation/inputs/<input_id>.json`；
- 补充记录：`QStandardPaths.AppLocalDataLocation/supplements/<supplement_id>.json`；
- 可用环境变量 `HCCFIT_DATA_DIR` 覆盖（测试用）。
- Key 只放输入框内存，不写 QSettings、不写日志、不写 `visit.json`；
- QSettings 只保存模式/场景/Base/Agent。

## 测试

```bash
python tests/smoke.py                 # 主窗口：确认场景卡 → stub → 导出 JSON/PDF → schema
python tests/test_input_normalization.py  # 规则/冲突/追问抑制/空格切分/schema 15 项
python tests/test_input_review.py         # 场景卡对话框（含“零追问”场景）
python tests/test_supplement.py           # 补充资料拼接/落盘
python tests/test_supplement_ui.py        # 补充重跑 + 新旧报告切换
python tests/test_router_client.py        # Router 场景映射
```

## 目录

```
hccfit_desktop/
├── main.py
├── app/
│   ├── paths.py       工作区、文件复制、数据目录
│   ├── runner.py      mode → brain/planner（不碰 HTTP）
│   ├── workers.py     RunWorker / ProbeWorker（QThread）
│   ├── export.py      PDF（QTextDocument+QPdfWriter）/ JSON
│   ├── input_normalization.py  单输入规则层（分句/术语/状态/追问抑制）
│   ├── input_review.py         场景卡对话框
│   ├── input_store.py          输入会话落盘
│   └── main_window.py          主窗口
├── core/              从 Web 版复制的业务模块（planner/sciencepal/contract/hcc_report…）
├── resources/terms.csv          术语/同义词表
├── resources/schemas/           visit + input_session schema
└── tools/input_demo.py          CLI 演示
```

## Windows 打包（P4 已完成）

```bash
pip install pyinstaller
python -m PyInstaller --noconfirm HCC-Fit.spec
```

产物：`dist/HCC-Fit/HCC-Fit.exe`（one-dir，约 114MB，整个 `HCC-Fit` 文件夹一起分发）。

自检（不显示窗口：确认场景卡 → stub → 导出 PDF，并构造场景卡对话框）：

```bat
set QT_QPA_PLATFORM=offscreen
HCC-Fit.exe --smoke
echo %ERRORLEVEL%   :: 0 = OK
```

已验证：打包后的 exe 在 Windows / Qt 默认平台下 `--smoke` 退出码 0。

## 后续

- 代码签名/安装包（可选）；
- macOS/Linux 字体与打包（Windows 验收后再做）。
