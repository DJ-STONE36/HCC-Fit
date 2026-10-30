# HCC-Fit × SciencePal Agent 接入规范与检索说明（2026-10-30）

> 依据官方文档 <https://sciencepal.ai/help/sdk>（API 与 Python SDK），结合本仓库实现整理。
> 目的：后续任何会话/同事改这块时，先读本篇，不用再爬官方页面或猜接口。
>
> 适用代码：`desktop/core/sciencepal.py`（桌面“大脑”）、`router/hccfit/sciencepal.py`（balanced 筛选）、
> `router/hccfit/strategies.py`、`router/hccfit/core.py`、`desktop/core/planner.py`。

---

## 1. 官方调用规则（必须遵守）

### 1.1 地址与鉴权

- Base：`https://sciencepal.ai/api`（测试站换成对应域名 + `/api`）。
- 所有请求（含 SSE 流）都要带：`Authorization: Bearer <API_KEY>`。
- 密钥在网页「用户中心 → 连接与开发 → API 与 Python SDK」生成，只显示一次；有效期 1–365 天。
- 密钥沿用账号权限与额度，**不要**写进前端、任务提示词或 URL。

### 1.2 发现 Agent

- `GET /api/agent-catalog?usable_only=true` → 响应 `agents[]`，字段含 `agent_id`、`name`、`source`。
- 列表包含内置、自建、获授权共享的 Agent；`client.agents.list()` 只列“自己创建的”，不代表没有可用 Agent。
- **调用时用 `agent_id`，不要用显示名称**。

### 1.3 选模型

- `GET /api/billing/available-models` → `models[].id`，保留 `platform::` 或 `user::` 前缀。
- **不要猜模型名**；不指定 `model_name` 时用平台默认模型。

### 1.4 新建会话与任务

```http
POST /api/agent/initiate          (form-data)
agent_id=<uuid>
prompt=<提示词>
stream=true
agent_select_type=manual
# 可选：model_name=<available-models 里的 id>；web_search_on=true/false
```

- 返回：`thread_id`、`agent_run_id`（没有 run 就不能继续）。
- **每次 initiate 都会新建会话**；多轮对话不要重复 initiate。

### 1.5 读流（回答正文在这里）

```http
GET /api/agent-run/{agent_run_id}/stream
Accept: text/event-stream
```

- SSE：每行 `data: <json>`，以 `data: [DONE]` 结束。
- 事件是结构化的（assistant / tool / status），不是一段纯文本；只取 assistant 增量。
- 实践要点：流默认从“当前尾部”订阅，**要带 `after_index=0` 从头重放**，否则在轮询结束后再读会拿到空流。

### 1.6 确认终态

```http
GET /api/agent-run/{agent_run_id}
```

- `status`：`completed` / `failed` / `stopped`；`error` 可能带原因。
- **流断开 ≠ 完成**；流超时先查 run，再决定是否重试，不要盲目新建任务。
- `asked`：Agent 在等用户回答，**不是完成**，不能当“还在跑”继续轮询。

### 1.7 回答追问 / 继续旧会话（asked 或已完成追问）

```http
POST /api/thread/{thread_id}/messages
{"message_id": "<新 UUID>", "content": "<用户回答>"}

POST /api/thread/{thread_id}/agent/start
{}
```

- 拿到**新的** `agent_run_id`，再读它的流；沿用原 `thread_id`。
- 同一条回答重试时复用同一个 `message_id` + 正文（幂等，不会写两条）。
- `add_message` 成功但 `start` 失败：只重试 start。
- `start` 超时：先 `list_thread_runs(thread_id)` 看是否已建 run，再决定是否重试。
- **不要**再调 `initiate()`（会新开会话、丢历史）；收到 `asked` 后应等用户作答，不要自动循环。

### 1.8 错误码 → 处置

| HTTP | 含义 | 处置 |
|---|---|---|
| 401 | Key 缺失/过期/撤销 | 重新生成 Key，检查是否同一账号/站点 |
| 403 / 404 | 无该 Agent 权限，或 Agent/接口不存在 | 核对 `agent_id`、账号授权、Base 地址 |
| 422 | 参数不被接受（常见：模型不可用） | 用 `available-models` 返回的 id，或留空用默认模型 |
| 429 | 额度用尽或被限流 | 查额度，退避后重试 |

排查时保留 HTTP 状态码、响应正文、`thread_id`/`run_id`；**绝不发送 API Key**。

---

## 2. 本仓库的实现映射

### 2.1 桌面“大脑”（五阶段 / 单次核查）

| 官方规则 | 实现位置 | 说明 |
|---|---|---|
| Base + Bearer | `SciencePalBrain._req` | 默认 `https://sciencepal.ai`，路径拼 `/api/...`；Key 先清洗不可见字符 |
| 发现 Agent | `_agent_id()` 直接返回内置 `DEFAULT_AGENT_ID`（HCC-Fit） | 不查 `/api/agents`；`SCIENCEPAL_AGENT_ID` 可覆盖，设空则退回按名称查找；403/404 自动降级为名称/平台路由 |
| initiate | `_initiate()` | multipart form 优先（支持附件）；422 缺字段自愈后重试；再退 JSON；带 `agent_id` + `agent_selection_explicit` + `agent_select_type=manual` |
| 读流 | `_replay_stream(after_index=0)` | 轮询完成后从头重放；按工具事件分段取最后一段正文 |
| 终态 | `_poll()` | completed/finished → 取正文；failed/error/stopped → 报错；**asked → 明确报错**（不空等） |
| 追问 | `_start_followup()` | `POST /thread/{tid}/messages` + `POST .../agent/start`；409 时等上一轮空闲再重试 |
| JSON 修复 | `repair_hcc_json()` | 同一 thread 内追加“只输出 JSON”的消息（不是重新 initiate） |
| 错误提示 | `_http_hint()` | 401/403/404/422/429 翻译成可操作中文 |

### 2.2 Router 的 balanced 筛选

| 官方规则 | 实现位置 | 说明 |
|---|---|---|
| initiate | `run_agent()` | form-data：`agent_id/prompt/stream=true/agent_select_type=manual`；`agent_id` 默认取 `DEFAULT_FILTER_AGENT_ID`（HCC-Fit）；`model_name` 仅在配置时传 |
| 读流 | `_stream_text(after_index=0)` | 立刻订阅 + 从头重放；跳过 status/tool 事件；`[DONE]` 结束 |
| 终态 | `_wait_terminal()` | completed/finished；failed/error/stopped → 报错；asked → 明确报错 |
| 回退 | `_answer_from_run()` / 再重放 | 流为空时先看 run 里的 messages，再重放一次 |
| 错误提示 | `_http_error()` | 401/403/404/422/429 → 中文提示 |

---

## 3. fast 与 balanced 的具体区别

两者**共用同一套算法召回**，区别只在“最后怎么筛/排”。

| 维度 | fast | balanced |
|---|---|---|
| 入口 | `POST /v1/search` `strategy=fast`（或 auto 且 deadline ≤15s） | `strategy=balanced`（或 auto 且 deadline >15s） |
| 1. 生成查询 | 规则：`core.build_plan()` 用结构化字段 + 原话关键词拼 **4 条 PubMed Boolean**（HCC ∧ 治疗 ∧ 结局 ∧ 模型词 + 验证/校准变体） | 完全相同 |
| 2. 召回 | `search_hybrid()`：并行 PubMed，每查询 50 条 → RRF 融合（k=60） | 相同；额外保留候选池 `pool`（前 10 篇）用于 LLM 筛选 |
| 3. 硬过滤 | `hcc_agent.filter_papers()` 规则过滤（终点/时间零点/年份/文献类型） | 相同（先过滤再交给 LLM） |
| 4. 排序/取舍 | 算法打分（匹配度/证据强度/可精读性 → 帕累托/分档）× top-k | **SciencePal filter agent**：逐篇判 `fit 0-2` + `keep/drop` + `reason`，只保留 `fit>=1`，再取 k |
| 5. 可读性 | `annotate_readability()` 查 PMC 全文/摘要；`require_fulltext` 时只留全文 | 相同（在最终结果上做一次） |
| 依赖 | 只需联网（PubMed / Europe PMC / PMC ID Converter），**不需要 Key** | 还需要 `SCIENCEPAL_API_KEY` + `SCIENCEPAL_FILTER_AGENT_ID` |
| 延迟 | 5–10 s | 60–90 s（含 LLM 轮询） |
| 成本 | 0 tokens | 每次 1 次 Agent 运行（tokens） |
| 可复现性 | 高（同输入 → 同候选；PubMed 索引变化除外） | 中（LLM 判断可能波动，`reason` 可解释） |
| 失败行为 | 上游不可用 → 空结果 + `empty_reason` | 无 Key / 无 agent / 422 / 429 / 超时 / asked → **回退算法 top-k**，`meta.degraded=true` + warning；不补造 |
| 输出差异 | `meta.llm_ms=0`、`strategy_used=fast` | `meta.llm_ms`、`filter_in/filter_out`、`degraded`、`strategy_used=balanced` |

**一句话**：fast = 规则检索 + 规则筛选；balanced = 同一批候选再用 Agent 做一次“医生式”筛选。balanced 不改变召回，只改变“哪些论文算数”。

### 3.1 提示词层面：fast 没有提示词，balanced 恰好一条

**fast：0 条提示词。** 从查询生成到排序全部是确定性代码：
`build_plan()` 拼 Boolean → `search_hybrid()` 检索/RRF → `filter_papers()`（规则）→ `score_all()` → `rank()`。
同样的输入，跑多少次结果都一样（PubMed 索引变化除外），可以拿日志逐条复核。

**balanced：1 条提示词**，位于 `router/hccfit/sciencepal.py` 的 `filter_papers()`，调用时连同“场景原话 + 候选列表”发给配置的 filter agent：

```text
你是 HCC 预测模型检索的医生式监督器。场景：
{scenario.text}

候选：
[{"pmid":"...","title":"...","year":2021,"journal":"...","abstract":"前 700 字","why":"...","risk":"..."}, ...]

按医生流程：真实预测/预后模型？结局、时间零点、人群/治疗是否匹配？
评分 fit 只取整数 0/1/2，含义固定：2=直接可用；1=有条件可用（有冲突或需补数据）；
0=不匹配/非预测模型/证据不足。keep 里只放 fit 1-2，fit 0 一律进 drop。
不要因标题有 nomogram 就保留。
只输出严格 JSON：
{"keep":[{"pmid":"","fit":0,"reason":""}],"drop":[{"pmid":"","reason":""}]}
```

逐句含义：

| 提示词片段 | 作用 | 为什么这么写 |
|---|---|---|
| “医生式监督器” | 给模型角色定位 | 要的是“像医生读摘要那样判断”，不是摘要改写 |
| “场景：{text}” | 患者侧约束 | 结局/时点/治疗必须和场景对齐，避免只看标题 |
| “候选：…” | 只喂题录+摘要前 700 字 + 算法给的 why/risk | 控制 token；不让它抓全文（S2 才读全文） |
| “真实预测/预后模型？” | 排除纯综述、纯机制、诊断模型 | 标题里有 nomogram 不等于可用 |
| “结局、时间零点、人群/治疗是否匹配？” | 四个匹配维度 | 与 S2 四维一致，提前在筛选阶段挡掉明显不匹配 |
| “fit 只取 0/1/2，keep 只放 1-2” | 给可执行标尺，且避免尺度歧义 | 实测过一种误判：理由写“匹配”却给 fit=0，导致全被丢弃；写明 2/1/0 含义后同一 Agent 稳定给 fit=2 |
| “不要因标题有 nomogram 就保留” | 反向抑制词面偏置 | 这是实测最容易出现的误召回 |
| “只输出严格 JSON…keep/drop” | 保证可解析 | 解析失败会被判为 `degraded` 并回退算法结果 |

执行与解析：

1. `run_agent()`：initiate（`agent_id`=filter agent、`stream=true`）→ 读 SSE → 确认终态；
2. `_parse()` 从回答里抠出 JSON；`strategies._pick()` 只保留 `fit>=1` 且 `pmid` 在候选池里的，
   再按 `fit` 取前 k；
3. 解析失败 / 超时 / 429 / asked → `degraded=true` + warning + 回退算法 top-k（**不会**补造论文）。

**只填 Key、不配 filter agent 时会怎样？**

| 情况 | 行为 |
|---|---|
| 账号可用 Agent 恰好 1 个 | 自动用它，warnings 提示“已自动使用可用 Agent：<name>”，`degraded=false`（前提是模型返回可解析 JSON） |
| 多个可用 Agent，其中恰好 1 个名称含 `filter/筛/审核/监督/screen/hcc` | 自动选它，并给出 warning |
| 多个且无法区分，或 0 个可用 | 不猜：`degraded=true` + warning，回退算法 top-k（**balanced 的 Agent 筛选不生效**），并提示显式配置 `SCIENCEPAL_FILTER_AGENT_ID` |
| 显式配置了 `SCIENCEPAL_FILTER_AGENT_ID` | 直接用它，不做自动发现 |

> 所以“只填 Key”**可能**真正跑通 balanced，但取决于账号里可用 Agent 的数量/命名；
> 想要稳定、可审计，仍建议显式配置一个专用 filter agent。

> 补充：若 filter 返回的 keep 全为空，`strategies.run` 会回退算法 top-k 并置 `degraded=true`（附 warning），
> 不返回空结果，交人工判断。
>
> 也就是说：**改“召回”不用碰提示词（改 `core.py` 的查询模板）；改“哪些论文算数”才动这条提示词**
> （或换一个 filter agent / 平台侧 Agent 配置）。


---

## 4. 五阶段流程（SciencePal Agent 模式）

### 4.1 总览

```
场景卡（已确认） + 论文文件
  → S0 域判定 → S1 论文选择 → S2 逐篇四维匹配 → S3 综合方案 → S4 期望补充数据
  → visit.json（四维表 / 证据强度 / 引文 / 原始返回 / 免责声明）
```

- 编排：`desktop/core/planner.py` 的 `Planner.run()` 顺序调用 `brain.run_stage(key, payload)`。
- 会话：S0 用 `initiate` 建 thread；S1–S4 在**同一 thread** 里 `add_message` + `agent/start` 追问（保留上下文，不重开会话）。
- 每阶段事件：`start` → `ok`（带耗时/摘要）或 `error`/`degraded`；前端“流程”面板实时显示。
- 每阶段输入都带：`scenario`、`files`，以及上一阶段/已纳入论文的结构化结果。
- 失败策略：Agent 未按 JSON 返回时 `_degrade()` 降级为原文摘要/空列表，链路继续（数据不够也要给东西）；不会因为某阶段失败而伪造结论。
- 超时：五阶段 180 s/阶段（单次核查 240 s），可在 `app/runner.py` 调。

### 4.2 各阶段

| 阶段 | 目的 | 输出（最小必需结构） | 降级 |
|---|---|---|---|
| **S0 域判定** | 判断场景是否属于“肝癌（肝脏原发肿瘤）的治疗或诊断相关” | `status`：`in_scope`/`outside_scope`/`unclear`；`note_zh` | 从原文关键词猜 `status`，`note_zh` 存摘要 |
| **S1 论文选择** | 从上传文件 + 平台检索结果里挑相关预测模型论文 | `papers[]`：`paper_id/title/doi/year/source/decision(include|exclude)/reason_zh/file_path` | `papers=[]`（后续按“无论文”继续） |
| **S2 逐篇匹配** | 每篇纳入论文做四维匹配：population / timepoint / input / outcome | `matched[]`：`paper_id`、`dimensions[]`（`id/status(match|mismatch|unknown)/reason_zh/quote/location`）、`tier(applicable|conditional|undetermined|not_applicable)`、`evidence_strength` | `matched=[]` |
| **S3 综合方案** | 综合匹配结果，给出可沟通的方案与注意点（**不给治疗建议**） | `summary_zh`、`options[]`（`name/basis_paper_ids/confidence/note_zh/what_it_needs`）、`cautions_zh[]` | 原文摘要 + “请人工判读” |
| **S4 期望补充数据** | 列出为提升可靠性还应补的化验/影像/病理/病史 | `expected_data[]`：`item_zh/why_zh/unlocks_zh/cost_hint_zh/kind` | `expected_data=[]` |

### 4.3 每阶段提示词的结构

`SciencePalBrain.run_stage()` 统一拼装：

1. `CONTRACT`（放宽版）：可用平台工具；**只要最后一条消息里有 ```json 代码块**；只要求下列字段，可中英文键名；不确定写 `unknown`，不要编造；不要输出 XML/function_calls。
2. 【本阶段任务】+【最小必需结构】。
3. 【场景】（不含 `paper_text` 的结构化场景）。
4. 【可用文件（临时位点，可直接读取）】。
5. 【论文正文片段（用户粘贴）】（截断 6000 字）。
6. 【上一阶段结果】（截断 4000 字）。

各阶段任务/结构见 `desktop/core/sciencepal.py` 的 `STAGE_PROMPTS`。

### 4.4 产物与降级标记

- `visit.json` 由 `Planner.run()` 组装：`domain`、`papers`、`matched`、`integrated`、`expected_data`、`uncertainties`、`provenance.stages_ms`、`disclaimer_zh`。
- `parse_mode`：`structured`（正常）/ `text_fallback`（某阶段只能拿原文）。
- `raw_answers[]`：每阶段原文（≤1500 字）+ 单次核查的“原始首次回答”`hcc_original`。
- 附件降级：平台 `workspace_context_prepare_failed` 时，桌面抽出 PDF 文本塞进 prompt 重跑，并标记 `degraded`。

### 4.5 与“HCC-Fit 单次核查”的区别

| | 五阶段 Agent | HCC-Fit 单次报告 |
|---|---|---|
| 入口 | `mode=sciencepal` | `mode=sciencepal_hcc` |
| 调用次数 | 5 次（同一 thread） | 1 次（`run_hcc_report`），必要时 +1 次 JSON 修复 |
| 任务 | 域判定→选论文→逐篇匹配→综合→补数据 | 一条 prompt 直接产出核查报告（四维/证据/引文/期望数据） |
| 输出 | 同一 `visit.json` schema | 同一 schema（`normalize_report`） |
| 缺 JSON | 每阶段独立降级 | 触发 `repair_hcc_json` 二次修复，成功后保留原始 Markdown |
| 适用 | 需要过程可解释、多篇论文 | 快速出单人单篇报告 |

---

## 5. 常见故障与排查

| 现象 | 常见原因 | 排查 |
|---|---|---|
| `balanced` 返回但 `degraded=true`，warning “SciencePal unavailable” | 无 Key / 无 filter agent / 422 模型不可用 / 429 / asked | 看 warning 原文；弹窗状态行看 Key/filter agent 是否“已配置” |
| `401` | Key 过期、复制带全角空格/零宽字符 | 桌面会提示“Key 形如 ……”并清洗不可见字符 |
| `403/404` | Agent 名称不匹配或 key 无权限 | 用 agent-catalog 确认 `agent_id`；单次核查模式 strict 会列出可见 Agent |
| `422` | 手填了不可用模型名 | 清空 `SCIENCEPAL_MODEL`（用平台默认），或从 available-models 复制 id |
| 运行完成但正文为空 | 流订阅从尾部、`tokens_used=0`、工具阶段结束就停 | 确认 `after_index=0`；看 `sp_raw/stream_*.json`；换 Agent 提示词要求“最后输出 JSON/正文” |
| Agent 追问（asked） | 提示词让 Agent 反问 | 提示词明确“不确定写 unknown，不要反问”；或换 Agent |
| 外部 Router 报 `WinError 10061` / “目标计算机积极拒绝” | 该地址没有服务在监听（通常是选了“外部 Router 服务”但没启动） | 对话里会提示“改用内置引擎重试”，一键切回；或双击包根目录 `启动 Router 服务.bat`（需先装 requirements） |

---

## 6. 本次修复记录（2026-10-30）

| 问题 | 修复 |
|---|---|
| Router 先轮询到 completed 再读流，且不带 `after_index` → 很可能拿到空流，balanced 静默降级 | `run_agent` 改为 **initiate(stream=true) → 立即读流（after_index=0）→ 确认终态**；仍为空则回退 run 结构/重放 |
| 两边都不识别 `asked`，空等到超时 | Router 抛“Agent 在追问”；桌面 `_poll` 同样明确报错（含 thread/run id） |
| Router 硬编码模型名 `platform::deepseek-v4-flash` | 默认留空（平台默认）；仅配置 `SCIENCEPAL_MODEL` 时才传 |
| initiate 缺 `agent_select_type=manual` | Router 与桌面都补上（桌面在显式选到 Agent 时） |
| 错误码只给原始正文 | 新增 401/403/404/422/429 → 中文可操作提示（两边） |
| 界面要填 Router token，普通用户被“鉴权”吓到 | 从界面移除；服务端默认不鉴权（`HCCFIT_ROUTER_TOKEN` 留空），仅远程部署用环境变量 |
| 无离线测试覆盖 | 新增 `router/tests/test_agent_client.py`（8 项）、`desktop/tests/test_sciencepal_client.py`（4 项） |

---

## 7. 复现与验证

```bash
# Router：Agent 客户端 + API 契约（全离线）
cd router && python -m unittest discover -s tests -v

# 桌面：客户端终态/错误码（离线）
cd desktop && python tests/test_sciencepal_client.py

# 真 Key 端到端（需要 Key，且 balanced 还需 filter agent）
set SCIENCEPAL_API_KEY=sp_live_xxx
set SCIENCEPAL_FILTER_AGENT_ID=<uuid>
HCC-Fit.exe --router-smoke          # fast，不消耗 LLM
curl -X POST http://127.0.0.1:8080/v1/search -H "Content-Type: application/json" \
  -d '{"scenario":{"text":"HCC 切除后 1 年复发","disease":"HCC"},"strategy":"balanced","k":3}'
# 期望：meta.degraded=false，warnings 为空，strategy_used=balanced
```
