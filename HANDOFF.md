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
| 五阶段 SciencePal | ✅ 已接线；真实 Key 需自测 |
| HCC-Fit 单次核查 | ✅ 已接线；真实 Key 需自测 |
| 四维 / 证据 / 引文 / 注意点 / 原始返回 | ✅ |
| PDF / JSON 导出 | ✅ |
| 报告后补充资料 | ✅ 已改为“所需资料只读文本框 + 每条资料一条输入框”；全量重跑；新旧报告切换 |
| Router 方案 A：先找候选论文 | ✅ 已加弹窗、候选列表、保存候选 md；**Router 服务未打包，需单独启动** |
| Router 弹窗填 SciencePal Key | ✅ 已加；仅 balanced 用，关闭清空 |
| Windows one-dir 构建 | ✅ `dist/HCC-Fit`（2026-10-30 已重建） |
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

### 3.1 桌面版源码

```bash
cd desktop
python -m pip install PySide6
python main.py
```

Windows 构建：

```bat
cd desktop
build_win.bat
```

或直接运行：

```text
desktop_build/HCC-Fit/HCC-Fit.exe
```

> one-dir 必须整个 `HCC-Fit` 文件夹一起复制，exe 不能单独拿走。

### 3.2 桌面版测试

```bash
cd desktop
python tests/test_input_normalization.py   # 15/15：规则/追问抑制/空格切分/schema
python tests/test_input_review.py          # 场景卡 UI（含“零追问”场景）
python tests/test_supplement.py
python tests/test_supplement_ui.py
python tests/test_router_client.py
QT_QPA_PLATFORM=offscreen python tests/smoke.py
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

### 3.3 Router 服务

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

### Router 方案 A

```
桌面“先找候选论文”
 → RouterDialog
 → HTTP /v1/search（fast 或 balanced）
 → 候选表
 → 保存 router_candidates_*.md
 → 加入 desktop 的 files
 → S1 仍由 SciencePal Agent 选论文
```

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

5. **Router 是否继续独立 HTTP 服务（待决策）**
   - 现状：可选前置，不替换 S1；候选 `.md` 只是摘要/题录；Router 服务不在 Windows 测试 zip 内；
   - 二选一：保持 HTTP sidecar，或改成直接 `import hccfit.strategies.run`（单 exe、无 8080）。

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
2. 跑桌面 6 个测试 + `--smoke`（见 3.2）；
3. 跑 Router 11 个测试；
4. ~~先做用户最近要求的三项~~ ✅ 已完成（AI 移除 / 追问抑制 / 空格切分）；
5. 再决定 Router：
   - 保持 HTTP sidecar；
   - 或改成直接 import `hccfit.strategies.run`（单 exe、无 8080）；
6. 真 Key 端到端（五阶段 + HCC 单次 + Router balanced）；
7. 再考虑 macOS / 安装包 / 签名。

### 判断“是否还要 Router 独立服务”

- 单机测试、想双击即用 → 推荐直接 import；
- Web/多端共用、Router 独立升级 → 保留 HTTP；
- 多用户 → 远程服务 + `HCCFIT_ROUTER_TOKEN`。

---

## 8. 关键文件

- 场景卡：`desktop/app/input_normalization.py`、`desktop/app/input_review.py`
- 追问抑制：`desktop/app/input_normalization.py` 的 `field_sufficient()` / `sufficient_fields()`
- 分句：`desktop/app/input_normalization.py` 的 `split_sentences()` / `_split_soft()` / `_join_on_space()`
- 补充资料：`desktop/app/supplement.py`、`desktop/app/main_window.py`
- Router 客户端：`desktop/app/router_client.py`、`desktop/app/router_dialog.py`
- 五阶段：`desktop/core/planner.py`、`desktop/core/sciencepal.py`
- Router 服务：`router/hccfit/`
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
| 构建 | PyInstaller one-dir 重建，`--smoke` 退出码 0 |
| 版本控制 | 新增 git 基线（`.gitignore` 排除构建产物与运行期数据），便于后续 diff/回滚 |

验证命令（在包根目录）：

```bash
cd desktop && python tests/test_input_normalization.py && python tests/test_input_review.py \
  && python tests/test_supplement.py && python tests/test_supplement_ui.py \
  && python tests/test_router_client.py && QT_QPA_PLATFORM=offscreen python tests/smoke.py
cd ../router && python -m unittest discover -s tests -v
```

---

*本包源码角色已从“只读快照”转为开发工作区；仍不含任何真实 Key 或患者工作区。*
