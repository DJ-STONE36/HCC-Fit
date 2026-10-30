# HCC-Fit 项目交接说明（2026-10-30）

> 给新会话/接手人：先读本文件，再按“启动与验证”跑一遍。
> 本包是**源码 + Windows 构建 + Router 服务 + 文档**的快照，不含真实 API Key、患者数据或测试工作区。
>
> **2026-10-30 接手更新（v2）**：交接单里的三项 P0 已完成——移除可选 AI 候选层、追问抑制（`sufficient`）、空格短句切分；测试 15/15、Router 11/11 全绿，Windows 构建已重建。详见第 5 节与第 9 节。

---

## 1. 现在做到哪一步

### 桌面版（主产品，PySide6）

| 模块 | 状态 |
|---|---|
| 单输入场景卡（一段话 → 字段） | ✅ 可用 |
| 规则抽取 + 术语表 | ✅ |
| 可选 AI 候选 | ✅ **已按用户要求移除**（代码、界面、测试、构建配置） |
| 追问 / 确认门禁 / 版本 +1 | ✅ **已实现“原话已有信息就不追问”**（`field_sufficient`） |
| 空格短句切分 | ✅ **已实现**（硬标点 + 空格软切分，保护 `3 年`/`58 岁`/`非 HCC`/`Child-Pugh A`） |
| 大脑模式 | ✅ **只剩 2 种**（五阶段 / HCC 单次）；“演示 / 本地模拟 / 人工中转”已从界面移除 |
| 五阶段 SciencePal | ✅ 已接线；真实 Key 需自测 |
| HCC-Fit 单次核查 | ✅ 已接线；真实 Key 需自测 |
| 四维 / 证据 / 引文 / 注意点 / 原始返回 | ✅ |
| PDF / JSON 导出 | ✅ |
| 报告后补充资料 | ✅ 已改为“所需资料只读文本框 + 每条资料一条输入框”；全量重跑；新旧报告切换 |
| Router 候选论文 | ✅ **已内置且是界面唯一入口**：进程内调用 `hccfit.strategies.run`，一键检索；外部 HTTP 代码保留但不显示 |
| SciencePal Key / Base / Agent | ✅ 只填 Key 即可：Base 与 Agent（`HCC-Fit`，`61033129-…-280a`）内置；filter agent 同样内置；均可用环境变量覆盖 |
| Windows one-dir 构建 | ✅ `dist/HCC-Fit`（2026-10-30 已重建，含内置 Router 与 requests） |
| macOS | ❌ 只有方案，未实施 |

### Simple Router（独立 FastAPI 服务）

| 项 | 状态 |
|---|---|
| fast / balanced / auto | ✅ |
| deep | ✅ 已移除/禁用，传 deep 400 |
| 非 HCC 门禁 | ✅ iCCA/肝转移/肝癌未明确 400 |
| 结构化字段进检索查询 | ✅ outcome/time_zero/treatment_state/horizon |
| why/risk 数组契约 | ✅ 代码/文档/TS/示例统一 |
| filter agent ID | ✅ 改为显式 env，空则明确降级；**真实 ID 仍需确认** |
| CORS / Router token | ✅ 白名单 + 可选 Bearer |
| k 1–8 | ✅ 超过 8 422 |
| API 测试 | ✅ 11/11 |
| 真实 SciencePal filter 端到端 | ❌ 本机无 Key，未验证 |
| Docker build | ❌ 本机无 docker，未实际构建 |

---

## 2. 目录结构

```
HCC-Fit/
├── HANDOFF.md                  ← 本文件
├── desktop/                    ← hccfit_desktop 源码（不含 dist/build）
│   ├── main.py
│   ├── app/
│   ├── core/
│   ├── tests/
│   ├── docs/                   ← 关键截图
│   ├── resources/terms.csv
│   ├── HCC-Fit.spec
│   └── README.md
├── desktop_build/
│   └── HCC-Fit/                ← Windows one-dir 构建（exe + _internal）
├── router/                     ← HCC-Fit_Simple_Router 源码
│   ├── hccfit/
│   ├── tests/
│   ├── README.md
│   └── HANDOFF_FRONTEND.md
├── docs/                       ← 方案/检查报告
└── manifest.sha256             ← 包内文件校验
```

**没有放进包：**

- `HCC-Fit_handoff_2026-10-27/` 原 Web 版及其 `workspaces/`：含历史测试患者数据，避免泄露。
- 任何 `.env`、真实 Key、`inputs/`、`supplements/`、`workspaces/`。
- `HCC-Fit_测试版_2026-10-29.zip`：单独测试包，未重复放入。

---

## 3. 启动与验证

### 3.0 最快打开方式（不用 Python）

双击包根目录的 **`启动 HCC-Fit.bat`**，或直接双击：

```text
desktop_build/HCC-Fit/HCC-Fit.exe
```

> one-dir 必须整个 `HCC-Fit` 文件夹一起复制/移动，exe 不能单独拿走。
> 可以把这个 exe 发送快捷方式到桌面；`启动 HCC-Fit.bat` 只是替你定位到它。

### 3.1 桌面版源码

```bash
cd desktop
python -m pip install PySide6 requests
python main.py
```

或双击 `desktop/run_desktop.bat`。

Windows 构建：

```bat
cd desktop
build_win.bat
```

### 3.2 桌面版测试

```bash
cd desktop
python tests/test_input_normalization.py   # 15/15：规则/追问抑制/空格切分/schema
python tests/test_input_review.py          # 场景卡 UI（含“零追问”场景）
python tests/test_supplement.py
python tests/test_supplement_ui.py
python tests/test_router_client.py
python tests/test_router_engine.py         # 7/7：内置 Router 引擎
python tests/test_modes.py                 # 4/4：大脑只剩 2 种
python tests/test_sciencepal_client.py     # 4/4：asked/错误码/initiate 参数
QT_QPA_PLATFORM=offscreen python tests/smoke.py
```

打包后自检（在 `desktop_build/HCC-Fit` 目录）：

```bat
set QT_QPA_PLATFORM=offscreen
HCC-Fit.exe --smoke          :: 0 = OK；失败原因见 AppLocalData\smoke.json
HCC-Fit.exe --router-smoke   :: 0 = 内置 Router 真实联网检索成功
```

无窗口自检：

```bash
QT_QPA_PLATFORM=offscreen HCCFIT_DATA_DIR=/tmp/hccfit_smoke python main.py --smoke
```

Windows：

```bat
set HCCFIT_DATA_DIR=D:\temp\hccfit_smoke
HCC-Fit.exe --smoke
echo %ERRORLEVEL%
```

### 3.3 Router

> 桌面版**已内置 Router**，正常使用不需要启动任何服务（见 4 节）。下面这些只在
> 你用“外部 Router 服务（HTTP）”模式、或要给 Web/多端共用时才需要。

测试：

```bash
cd router
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

启动：

```bat
set SCIENCEPAL_API_KEY=sp_live_xxx
set SCIENCEPAL_FILTER_AGENT_ID=真实的-filter-agent-uuid
set HCCFIT_ROUTER_TOKEN=change-me
uvicorn hccfit.app:app --host 127.0.0.1 --port 8080
```

健康检查：

```bash
curl http://127.0.0.1:8080/healthz
```

期望：

```json
{"sciencepal_configured": true, "sciencepal_filter_configured": true}
```

> `fast` 不需要 SciencePal Key；`balanced` 需要 Key + filter agent ID。
> `HCCFIT_ROUTER_TOKEN` 是服务鉴权，不是 SciencePal Key。

---

## 4. 核心架构

### 桌面版

```
一段话
 → RuleExtractor.extract()
 → InputSession（字段/状态/来源/确认）
 → 场景卡（可编辑、追问、确认）
 → to_agent_scenario()
 → Runner / Planner
 ├─ sciencepal：S0–S4 五阶段
 └─ sciencepal_hcc：单次核查
 → visit.json → 四维/PDF/JSON
```

### 补充资料

```
报告 expected_data
 → 只读资料文本框 + 每条资料一个输入框
 → Supplement（逐项 value）
 → build_supplemented_scenario()
 → 新 workspace 全量重跑
 → 新 visit；旧 visit 保留，可切换
```

### Router（已内置到桌面）

```
桌面“找候选论文（内置 PubMed）”
 → RouterDialog（引擎状态：可用/Key/filter agent）
 → app/router_engine.py → 进程内 hccfit.strategies.run（fast 或 balanced）
 → 候选表 → 保存 router_candidates_*.md → 加入 desktop 的 files
 → S1 仍由 SciencePal Agent 选论文
```

- 默认**进程内直连**：不用起 uvicorn、不占 8080、不需要 `HCCFIT_ROUTER_URL`；
- 打包时 `router/hccfit` 作为数据进入 `_internal/router`，`app/router_engine.py` 依次找
  `HCCFIT_ROUTER_DIR` → `_MEIPASS/router` → `desktop/../router` → `cwd/router`；
- 外部 HTTP 服务（`router/` + `RouterClient`）作为核心代码保留，但**界面不再暴露**（无“检索方式/地址/token”入口），供多端/远程/命令行使用；
- `router/` 仍是同一份源码：桌面只是**导入**它，没有复制第二份。

**不是 Router 替换 S1**；候选只是题录/摘要，S2 仍需 PDF/正文。

---

## 5. 未完成项与本次已完成项

### ✅ 已完成（2026-10-30 接手批次）

1. **去掉“可选 AI 补充”**
   - 删除 `app/ai_extract.py`；删除 `input_review.py` 里的 AI checkbox/Base/模型/Key/按钮与 `_ai_fill`；
   - 删除 `input_normalization.py` 里的 `AIProvider` / `apply_ai_candidates` / `extract_with_ai` / `PROTECTED`；
   - `HCC-Fit.spec` 的 hiddenimports 去掉 `ai_extract`；测试与 README 同步；
   - 现在输入层**只走规则/术语表/医生确认**，不调用任何模型。

2. **原话已有对应信息时不生成追问（`sufficient`）**
   - 新增 `field_sufficient()` / `sufficient_fields()`；追问只在信息不足时生成；
   - 疾病：只有明确 HCC 才算足够（“肝癌/肝脏肿瘤/恶性肿瘤”仍追问）；
   - 治疗：明确切除/TACE/消融/系统治疗/肝移植；“刚切完”不追问治疗大类；
   - 结局：明确 OS / 复发（RFS）；只说“生存/预后”仍追问；
   - 起点：明确手术日/治疗日/今天；问题：原话已写清楚想问什么；
   - 场景卡新增“信息”列，逐字段显示“足够/—”；`input_session.json` 新增 `sufficient` 与 `concept_id`；
   - 补充：原话完全没提疾病时也会追问疾病（此前没有入口，属于静默漏问）。

3. **空格短句切分**
   - `split_sentences()` = 硬标点（`。！？；换行`）+ 空格软切分；
   - 保护：数字+单位（`3 年`、`58 岁`、`1.5 cm`）、否定+内容（`非 HCC`）、拉丁复合名（`Child-Pugh A`、`hepatocellular carcinoma`）；
   - 多个空格/全角空格/制表符视为明确分隔；
   - 顺带支持“3 年”“6 个月”这类带空格的范围写法（→ 36 个月 / 6 个月）。

### P1 / 待决策

4. **真实 filter agent ID / Key 未验证**
   - 默认 filter agent ID 已去掉全零占位；
   - 需真实 `SCIENCEPAL_FILTER_AGENT_ID` + Key 跑一次 balanced，确认 `degraded=false`。

5. ~~Router 是否继续独立 HTTP 服务~~ ✅ **已定：内置进程内引擎**
   - `app/router_engine.py` 直接 `import hccfit.strategies` 并调用 `run()`；界面“检索方式”默认内置；
   - 打包时 `router/hccfit` 作为数据进 one-dir；开发态找包内同级 `router/`；
   - 外部 HTTP 模式保留（切下拉或设 `HCCFIT_ROUTER_URL`），供多端/远程部署；
   - 注意：`router/` 与 `desktop/` 必须是同级目录（打包/开发都依赖这个相对位置）。

6. **Docker 未实际 build**
   - Dockerfile 已改非 root + healthcheck + `.dockerignore`，但本机没有 docker。

### 其他

7. macOS 只有方案，未实施。
8. Windows exe 未签名；SmartScreen 提示；无安装包。
9. 真实 SciencePal 五阶段 / HCC 单次在**新 exe**里的端到端，需要再跑一次（本机无 Key，只跑了 stub `--smoke`）。
10. Router 的 `run_id` 校验、prompt 注入防护、凭据日志等 P2 未做。

---

## 6. 安全与数据

- 包里没有真实 Key；`router/.env.example` 只是占位。
- 不要把 `HCCFIT_APP/workspaces`、`desktop` 的 `inputs/`、`supplements/`、`workspaces/` 打进包。
- 如果之前在聊天/日志里出现过 `hccfit_…` 或 `sp_live_…`，建议轮换。
- 桌面 Key 只放内存，不写 QSettings、日志、`visit.json`。
- 临床测试请用模拟/脱敏数据。

---

## 7. 建议新会话的接手顺序

1. 读 `HANDOFF.md`、`desktop/README.md`、`router/README.md`；
2. 跑桌面 9 个测试 + `--smoke`（见 3.2）；
3. 跑 Router 19 个测试；
4. ~~先做用户最近要求的三项~~ ✅ 已完成（AI 移除 / 追问抑制 / 空格切分）；
5. ~~再决定 Router~~ ✅ 已定：**内置进程内引擎**，一键检索；外部 HTTP 仍可选；
6. 真 Key 端到端（五阶段 + HCC 单次 + Router balanced）；
7. 再考虑 macOS / 安装包 / 签名。

### Router 形态（已决定）

- 默认：**进程内直连**（单 exe、无 8080、双击即用），这是当前实现；
- 外部 HTTP 服务仍保留在核心代码里（`router/` + `RouterClient`），但界面只提供内置引擎；
  多端/远程部署时用 `curl` 或 `RouterClient` 直连。服务端默认不鉴权（`HCCFIT_ROUTER_TOKEN` 留空）。

---

## 8. 关键文件

- 场景卡：`desktop/app/input_normalization.py`、`desktop/app/input_review.py`
- 追问抑制：`desktop/app/input_normalization.py` 的 `field_sufficient()` / `sufficient_fields()`
- 分句：`desktop/app/input_normalization.py` 的 `split_sentences()` / `_split_soft()` / `_join_on_space()`
- 补充资料：`desktop/app/supplement.py`、`desktop/app/main_window.py`
- 内置 Router：`desktop/app/router_engine.py`、`desktop/app/router_dialog.py`、`desktop/app/router_client.py`
- 五阶段：`desktop/core/planner.py`、`desktop/core/sciencepal.py`
- SciencePal 接入规范（官方规则 / fast vs balanced / 五阶段 / 错误码）：`docs/HCC-Fit_SciencePal接入与检索说明_2026-10-30.md`
- Router 服务：`router/hccfit/`（同一份源码，桌面直接 import，不复制）
- 输入会话 schema：`desktop/resources/schemas/input_session.schema.json`
- Router 检查报告：`docs/HCC-Fit_Simple_Router_检查报告_2026-10-29.md`
- 输入归一方案：`docs/HCC-Fit_输入归一方案_2026-10-29.md`
- 补充资料方案：`docs/HCC-Fit_报告后补充资料方案_2026-10-29.md`
- 桌面方案：`docs/HCC-Fit_PyQt桌面化方案_2026-10-29.md`
- macOS 方案：`docs/HCC-Fit_macOS适配方案_2026-10-29.md`

---

## 9. 本次接手变更记录（2026-10-30）

| 项 | 结果 |
|---|---|
| 基线验证（改动前） | 桌面 6/6 通过、Router 11/11 通过 |
| AI 候选层移除 | `app/ai_extract.py` 删除；`input_normalization.py` 删 3 个 AI 函数；`input_review.py` 删 AI 行；spec 同步 |
| 追问抑制 | 新增 `field_sufficient()`；场景卡第 7 列“信息”；`sufficient` 写入 input_session |
| 空格软切分 | `split_sentences()` 重写；保护数字+单位/否定/拉丁名；支持“3 年”→36 个月 |
| 术语表 | 新增 `treatment.transplant`（肝移植） |
| 测试 | 输入归一 12 → 15 项（含 schema 校验、AI 已移除断言）；场景卡加零追问场景 |
| 文档截图 | `desktop/docs/input_review.png` 重拍（7 列、显示“信息/足够”，无 AI 行） |
| **大脑模式精简** | 界面只剩 `sciencepal` / `sciencepal_hcc`；删除 ManualBrain（人工中转）、mock_sp（本地模拟）与 mock_sciencepal.py；`stub` 只留给自动化测试（`select_mode("stub")`）；旧报告里的 manual/mock_sp 会明确报错而不是静默出演示数据；新增 `tests/test_modes.py`（4/4） |
| **Router 内置** | 新增 `app/router_engine.py`（进程内 `hccfit.strategies.run`）；对话框“检索方式”默认内置、一键检索；spec 把 hccfit + toolkit 冻进 exe（`requests`/`xml.etree` 等依赖由静态分析收集）；外部 HTTP 仍可选；新增 `tests/test_router_engine.py`（7/7） |
| 构建 | PyInstaller one-dir 重建（119MB，含 Router + requests）；`--smoke` 与 `--router-smoke` 均退出码 0（后者真实联网取回 3 篇 PubMed 候选） |
| 版本控制 | 新增 git 基线（`.gitignore` 排除构建产物与运行期数据），便于后续 diff/回滚 |
| **SciencePal 调用修正** | Router 改为 initiate(stream=true) → 立即读流(after_index=0) → 确认终态，修掉“轮询完再读流拿空答案 → balanced 静默降级”；支持 asked/failed/stopped；模型默认留空；补 `agent_select_type=manual` 与 401/403/404/422/429 中文提示 |
| **桌面端** | `_poll` 识别 `asked`（明确报错，不再空等）；新增 `_http_hint`；initiate 补 `agent_select_type` |
| **Router token** | 界面不再要求填 token；服务端默认不鉴权（留空），仅远程部署用 `HCCFIT_ROUTER_TOKEN` |
| **界面收敛** | 去掉“检索方式/外部地址/token/filter agent”输入，只保留内置引擎；Key 与模式选择合并为左侧最上方“1 · SciencePal 连接”（场景之上，已去掉“大脑”字样）；Key 空/非 ASCII/空格实时红字警告，启动与 balanced 会拦截 |
| **fast vs balanced 文档** | 接入说明新增 3.1：fast 0 条提示词（全规则），balanced 1 条提示词（逐句解释 + 解析/降级路径） |
| **只填 Key 的 balanced** | 未配 `SCIENCEPAL_FILTER_AGENT_ID` 时自动从可用 Agent 里选（唯一可用，或名称含 filter/筛/审核/HCC 的唯一匹配）；无法唯一确定才降级 fast，并提示显式配置；成功时 warning 标注“已自动使用…” |
| **候选对话框修复** | “打开 PMC/PubMed”未选行时默认打开第一篇、并有浏览器失败兜底；标题列不再截断（行高随内容换行）；保存候选出错不再静默（弹错误框），未勾选时可一键保存全部；新增 3 项 UI 测试 → test_router_engine 9/9 |
| **对话框修复 2** | 打开链接改为 `startfile`→Qt→webbrowser 三级兜底并给出可见反馈；标题列加宽、风险列收窄、其余列固定/可调且全部换行，行高在布局完成后统一计算（修掉超高行）；数据目录改为“探针可写”选择，`LocalLow` 不可写时自动回退（解决保存 PermissionError）；新增 test_paths（2 项） |
| **真 Key 端到端验证（2026-10-30）** | Key 有效（51 位）；账号 6 个可用 Agent，指定的 `61033129-…-280a` 为自定义「HCC-Fit」且在列表中；`/billing/available-models` 5 个模型；balanced 实测 completed（36–41s，含真实 keep/drop）；发现 fit 尺度歧义（理由写“匹配”却给 fit=0 → 全丢）→ 提示词固定 0/1/2 含义后同一 Agent 稳定给 fit=2；keep 全空时回退算法 top-k 并置 degraded |
| **内置 Agent** | 桌面 `DEFAULT_AGENT_ID` 与 Router `DEFAULT_FILTER_AGENT_ID` 均指向实测可用的 `HCC-Fit`；界面去掉 Base/Agent 输入，只留 Key；`SCIENCEPAL_AGENT_ID` / `SCIENCEPAL_FILTER_AGENT_ID` 可覆盖，设空则回退查找/自动路由；Key 无权使用内置 Agent（403/404）时自动退回按名称/平台路由 |
| **逐篇发送 + 文案** | 标题去“大脑”；测试连接成功只显示“能连上”；新增复选框“多篇论文时逐篇发给 Agent”：五阶段逐篇 S2、单次核查逐篇出报告后合并 papers/matched/期望数据（默认一次带全部）；新增 test_split_papers 4 项 |
| **逐篇进度行** | 流程面板支持动态子行：逐篇事件用 `match#i` / `hcc#i`，插在父行之后，各自显示“运行中/完成 · tier · 耗时”；每次启动前重建流程行，避免上次残留；新增 3 项测试 |
| **口述例子回归** | 用 5 个临床例子检验并补齐：术后时长支持年/月/周/天与“一个月复查”；“刚开完刀/根治性手术”映射切除+术后；额外抽取肿瘤大小、癌栓/镜下癌栓、BCLC、分期范围；意图改为疑问句评分并保留并列问题；“总的生存期”判为明确 OS。新增 test_clinical_examples（5 项） |
| **预测范围追问** | 缺 `horizon` 时新增 `q_horizon`（1 年/3 年/5 年/自定义/不知道）；场景卡把答案写入 horizon（36个月等），自定义“2 年”也能换算；新增 2 项测试（规则 + UI） |
| **官方示例更新** | 场景示例改为“58 岁男性…从今天开始…三年总生存率…请说明是否 HCC…”；同时修正“是否 HCC”被误当诊断的问题（待确认表述→疾病 ambiguous 并追问）；新增示例回归 |
| **原始返回默认隐藏** | “就诊反馈”右上角新增“显示原始返回”，默认不占页签；降级/二次修复时自动显示；数据仍写入 visit.json 的 raw_answers（PDF 不含）；设置记忆；新增显隐测试 |
| **接入文档** | 新增 `docs/HCC-Fit_SciencePal接入与检索说明_2026-10-30.md`（官方 8 条规则、fast vs balanced、五阶段、错误码、本次修复） |
| **测试** | Router 新增 `test_agent_client.py`（8 项，全离线）→ 19 项；桌面新增 `test_sciencepal_client.py`（4 项）→ 9 个脚本 |

验证命令（在包根目录）：

```bash
cd desktop && python tests/test_input_normalization.py && python tests/test_input_review.py \
  && python tests/test_supplement.py && python tests/test_supplement_ui.py \
  && python tests/test_router_client.py && python tests/test_router_engine.py \
  && QT_QPA_PLATFORM=offscreen python tests/smoke.py
cd ../router && python -m unittest discover -s tests -v
```

---

*本包源码角色已从“只读快照”转为开发工作区；仍不含任何真实 Key 或患者工作区。*
