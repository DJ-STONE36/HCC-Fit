# HCC-Fit Agent 使用说明 × 本地软件 · 匹配度检查（2026-10-28）

> 检查对象：`HCC-Fit使用说明.md`（v1.0，249 行）
> 对照对象：`HCC-FIT_APP` 的 SciencePal 五阶段模式、`visit.schema.json`、`contract.py`
> 结论：**概念一致、契约不一致，按这份文档直接对接约 45%；不能塞进现有 SciencePal 五阶段模式，需要单次调用 + 映射层。**

---

## 0. 先说清楚：这份文档描述的是哪套系统

文档写的是：

- `GET /api/health`
- `POST /api/check`（multipart，`X-API-Key`）
- `POST /api/check_json`（JSON，`X-API-Key`）
- 端口 5000、Flask、`client_example.py`

这是 **`hcc_fit_api` 这个 Flask 服务**的说明书，**不是 SciencePal 平台的 agent runtime**（后者是 `/api/agent/initiate`、`/api/agent-run/{id}/stream`、`Authorization: Bearer`）。

所以：

| 你想接的 | 该用哪套 | 这份文档是否适用 |
|---|---|---|
| 本地/内网 Flask 服务 | `POST /api/check_json` + `X-API-Key` | ✅ 适用 |
| SciencePal 上的 Agent | `/api/agent/initiate` + Bearer | ❌ 文档没写这套 |

你之前那个 `hccfit_…` 令牌，正好对得上文档里的 `HCC_FIT_API_KEY`（`X-API-Key`）；它在 SciencePal 上 401 是正常的——**它从来不是 SciencePal 的 Key**。

---

## 1. 输入对照

### 1.1 文档要求（4 项）

| 字段 | 说明 | 必填 |
|---|---|---|
| `paper_text` / `file` | 论文全文或 PDF（**单篇**） | 必填（二选一） |
| `target_population` | 目标患者人群，如“接受手术的 HCC 患者” | 必填 |
| `clinical_timepoint` | 临床时点，如“术前” | 必填 |
| `target_outcome` + `outcome_timeframe` | 结局及时间范围，如“3 年总生存期”/“3 年” | 必填（timeframe 可选） |

### 1.2 软件实际发送（SciencePal 模式）

`app.js` 收集 6 个场景字段 + 文件 + `paper_text`，`planner.py` 再拆成 **5 个阶段**、每个阶段一段不同的 prompt（`sciencepal.py:644-661`），并把 `paper_text` 截到 **6000 字符**（`sciencepal.py:687`）。

### 1.3 逐项映射

| 文档字段 | 软件字段 | 能否直接映射 | 说明 |
|---|---|---|---|
| `target_population` | `scenario.patient_summary_zh` | ⚠️ 勉强 | 软件给的是“58 岁男性，HBV 相关 HCC，8.2cm…”整段病例；文档要的是“接受手术的 HCC 患者”这种人群短语。Agent 可能把整段当人群，或抓不住重点 |
| `clinical_timepoint` | `scenario.use_timepoint` | ❌ 需转换 | 软件是枚举 `pre_surgery/pre_treatment/post_surgery/at_recurrence/surveillance`；文档要中文“术前/术后/复发后/随访”。不转换，Agent 可能不理解 |
| `target_outcome` | `scenario.outcome_type` | ⚠️ 可用 | 软件是自由文本（“1 年内复发”“OS”）；文档要“3 年总生存期”这种带时间范围的表述 |
| `outcome_timeframe` | — | ❌ 无 | 软件没有独立的时间范围字段，只能从 `outcome_type` 里拆或留空 |
| `paper_text` / `file` | `paper_text` + 上传文件 | ⚠️ 部分 | 单篇可对；**多篇时文档不支持**，软件会把多个文件一起发 |
| — | `scenario.task_zh`（要回答的问题） | ❌ 丢失 | 文档没有这个字段。这是软件最核心的“临床问题”，Agent 拿不到，可能答偏 |
| — | `scenario.inputs_given` / `inputs_missing` | ❌ 丢失 | 文档没有对应字段；而这两个恰好是“输入可得性”判断的关键信息 |

**输入匹配度：约 50%。** 3 个字段能对上（还需枚举/措辞转换），`task_zh`、`inputs_given/missing`、多篇、时间范围都没有落点。

> 两个实现细节：
> - Flask API 的 PDF 走 `POST /api/check`（`file`），纯文本走 `POST /api/check_json`；`/api/check` **不接受** `paper_text`，不要混用。
> - 软件现在把 `paper_text` 截到 6000 字符再发给 SciencePal；如果改走 Flask API，不要沿用这个截断，应发完整文本（API 自己会在 50,000 字符处截断）。

---

## 2. 输出对照

### 2.1 文档返回的结构（7 块）

```
population_check   {paper_population, target_population, comparison, verdict, evidence[]}
timepoint_check    {predictors[], paper_timepoint, target_timepoint, availability_analysis, verdict, evidence[]}
outcome_check      {paper_outcome, target_outcome, comparison, verdict, evidence[]}
validation_evidence{development_performance, internal_validation, external_validation, validation_population, not_reported[]}
overall_verdict    match|mismatch|unclear
overall_summary
limitations[]
```

### 2.2 软件需要的结构（`visit.schema.json`）

```
domain{status,note}
papers[]{paper_id,title,decision,reason}
matched[]{paper_id, dimensions[4]{id,status,quote,location}, tier, evidence_strength}
integrated{summary_zh, options[], cautions_zh[]}
expected_data[]{item_zh,why_zh,unlocks_zh,cost_hint_zh,kind}
uncertainties[]   ← 至少 1 条
parse_mode / raw_answers / provenance / disclaimer
```

### 2.3 逐项映射

| 文档字段 | 软件字段 | 能否直接映射 | 说明 |
|---|---|---|---|
| `population_check.verdict` | `dimensions[population].status` | ✅ | `match/mismatch/unclear` → `match/mismatch/unknown`（注意 `unclear→unknown`） |
| `population_check.evidence[]` | `dimensions[population].quote/location` | ⚠️ | 取第一条；多条会丢 |
| `timepoint_check.verdict` | `dimensions[timepoint].status` | ✅ | 同上 |
| `timepoint_check.predictors` + `availability_analysis` | `dimensions[input]` | ❌ **关键缺口** | 文档没有独立的 `input.verdict`；只能从一段中文里猜“有变量不可得”，或者扩展文档 |
| `outcome_check.verdict` | `dimensions[outcome].status` | ✅ | 同上 |
| `validation_evidence` | `evidence_strength` | ⚠️ 需规则 | 文档给的是文本（开发集/内部/外部验证），软件要 `strong/moderate/weak/insufficient` 枚举 |
| `overall_verdict` | `tier` | ❌ 需规则 | 文档是 3 值，软件是 4 档（applicable/conditional/undetermined/not_applicable） |
| `overall_summary` | `integrated.summary_zh` | ✅ | 直接 |
| `limitations[]` | `integrated.cautions_zh` + `uncertainties` | ✅ | 至少 1 条即可满足 schema |
| — | `papers[]`（标题/DOI/年份/纳入排除） | ❌ 缺失 | 文档没有论文元数据；只能用文件名兜底、`decision=include` |
| — | `domain`（S0 域判定） | ❌ 缺失 | 文档默认就是 HCC，没有域闸门；只能置 `in_scope` 并写备注 |
| — | `expected_data[]`（还缺什么数据） | ❌ 缺失 | 文档没有这个概念——**产品核心能力之一会空掉** |
| — | `integrated.options[]` | ✅ 可空 | 文档本来就不给治疗建议，空数组正确 |
| — | `raw_answers` / `parse_mode` | ✅ 适配层可填 | 把原始 JSON 存进去 |

**输出匹配度：约 50%。** 三个 verdict + 证据能对上；`input` 维度、`tier`、`expected_data`、`papers`、`domain` 都要补规则或扩展契约。

### 2.4 最关键的语义差异：两套“四维”不是同一个四维

| 文档的四维 | 软件的四维 |
|---|---|
| 人群 | 人群 |
| 时点 | 时点 |
| 结局 | **输入（变量可得性）** |
| **验证证据** | 结局 |

- 文档把“输入可得性”折进了 `timepoint_check.availability_analysis`，没有独立判定；
- 文档把“验证证据”当成第四维，而软件**没有“验证”这一维**，只有一个 `evidence_strength` 枚举（strong/moderate/weak/insufficient）。

所以 Agent 的报告即使字段齐全，**直接塞进软件的矩阵也会少一维、错一位**。

---

## 3. 协议 / 鉴权对照

| 项 | 文档（Flask） | 软件 SciencePal 模式 | 匹配 |
|---|---|---|---|
| 入口 | `POST /api/check_json`（5000） | `/api/agent/initiate` + poll + stream | ❌ |
| 鉴权 | `X-API-Key: <HCC_FIT_API_KEY>` | `Authorization: Bearer <SciencePal Key>` | ❌ |
| 调用次数 | 1 次/篇 | 5 次（S0–S4） | ❌ |
| 输入单元 | 单篇 | 多篇 | ❌ |
| 返回 | 一次性 JSON | 流式分片、两层 JSON、需要拼接 | ❌ |
| 失败 | HTTP 4xx/5xx + `{"error"}` | 流里 `status=failed` / 401 | ⚠️ |

**协议/鉴权匹配度：约 5%。** 这就是为什么把 `hccfit_…` 填进 SciencePal 输入框会 401：两套系统完全不是一条链路。

---

## 4. 文档自身与代码的不一致（顺手发现）

| # | 文档说 | 代码实际 | 影响 |
|---|---|---|---|
| 1 | `LLM_API_KEY` 环境变量（默认 `SILICONFLOW_API_KEY`） | `hcc_fit_engine.py:98` 只读 `SILICONFLOW_API_KEY`；`app.py:24` 的 `create_engine()` 没传参 | 只设 `LLM_API_KEY` **不生效**，核查会 401 |
| 2 | `/api/check` 支持 `file` 与 `paper_text` 二选一 | `app.py:383-391` 只读 `file`，没有 `paper_text` 分支 | 只传 `paper_text` 调 `/api/check` 会报“请上传 PDF” |
| 3 | “四个维度：人群/时点/结局/验证证据” | 实际只有 3 个 `*_check` + 1 个 `validation_evidence`，没有 `input_check` | 软件的 `input` 维度无源 |
| 4 | “论文全文” | `hcc_fit_engine.py:178` 截断到 50,000 字符 | 长论文后半段丢失，可能影响证据 |
| 5 | 默认 `0.0.0.0:5000` + 未设令牌则关闭鉴权 | `app.py:453`、`app.py:35` | 局域网内谁都能调；必须设 `HCC_FIT_API_KEY` 或改绑 `127.0.0.1` |

---

## 5. 匹配度评分

| 方面 | 分数 | 结论 |
|---|---|---|
| 概念与安全边界 | 90% | 都强调证据、不诊断、不推荐治疗 |
| 输入字段 | 50% | 3/4 可映射；`task_zh`、`inputs_given/missing`、多篇、时间范围缺失 |
| 输出维度 | 50% | 3 个 verdict 可映射；`input`、`tier`、`expected_data`、`papers`、`domain` 缺 |
| 协议/鉴权 | 5% | Flask `X-API-Key` vs SciencePal Bearer |
| **总体（直接对接）** | **约 45%** | **不能直接塞进现有模式，需要适配层** |

---

## 6. 当前会出现的现象

- 把文档的 `hccfit_…` 填进 **SciencePal API Key**：401（Key 类型不对）；
- 即使 Key 换成 SciencePal 的、Agent 返回文档这套 JSON：走五阶段模式时，S0 找不到 `status`、S1 找不到 `papers`、S2 找不到 `matched` → 四维空、报告落到 `text_fallback`；
- 报告里不会出现“还缺什么数据”（`expected_data` 无源）；
- 引文不会显示（软件本来就未渲染 `quote/location`，见报告 D6）。

---

## 7. 解决方案（两条，共用一层映射）

### 方案 A：直接对接这份 Flask API（与你手里的 `hccfit_…` 匹配）

```
APP → POST http://127.0.0.1:5000/api/check_json
       X-API-Key: hccfit_…
       {paper_text, target_population, clinical_timepoint, target_outcome, outcome_timeframe}
   → 映射成 visit.json → 现有 UI 渲染
```

- 新增 `hcc_api` 模式（不是 SciencePal 模式）；
- 输入映射：`patient_summary_zh`、`use_timepoint→中文`、`outcome_type`、`paper_text`；
- 输出映射：见 §2.3；
- `input` 维度：先用 `availability_analysis` 启发式，或后续扩展 API；
- 前提：服务要跑起来 + `SILICONFLOW_API_KEY`。

### 方案 B：走 SciencePal Agent（你已有可用 Key 的话）

- 新增“单次核查”模式（一次 initiate + 轮询 + 流解析），不要用五阶段；
- 提示词要求 Agent 返回**统一 schema**（建议加 `input_check` 和 `expected_data`）；
- 复用同一套映射；
- 前提：Agent 的 system prompt 可改，或它本来就返回文档这套结构。

### 建议先补的两块契约

1. **`input_check`**：逐变量“目标时点是否可得”，不要从中文里猜；
2. **`expected_data[]`**：还缺什么数据、补齐解锁什么。

补完后，映射可以做到 1:1，软件的“四维 + 还缺什么”才完整。

---

## 8. 工作量与验收

| 阶段 | 内容 | 预估 |
|---|---|---|
| P1 | `hcc_report.py` 映射 + 单次模式（Flask 或 SciencePal） | 1 天 |
| P2 | UI：四维引文、验证证据、期望数据 | 0.5–1 天 |
| P3 | 假服务测试 + 真 Key 端到端 | 0.5 天 |

验收：

- `visit.schema.json` 对 match/mismatch/unclear 三种样本均 0 错；
- 四条自测仍 31/31；
- `task_zh` 不再丢失（要么传给 Agent，要么明确不用）；
- 引文可见、`expected_data` 有内容（或明确显示“API 未提供”）。

---

## 9. 待确认

1. 你最终要接的是**这份 Flask API**，还是 **SciencePal 上的 Agent**？
2. Agent/服务的 system prompt 能不能改？能改的话，我直接给统一 schema；
3. 多篇论文是刚需吗？文档只支持单篇；
4. `SILICONFLOW_API_KEY` 你有吗？没有的话，Flask 服务只能启动、不能核查。

---

## 10. 落地记录（2026-10-28）：SciencePal 单次模式已实现

按“走 SciencePal Agent”的路线，已经加了 **`sciencepal_hcc`（SciencePal · HCC-Fit 单次报告）** 模式：

- **一次调用**：`initiate → 轮询 → /stream?after_index=0`，不再拆 S0–S4；
- **提示词**：包含软件的场景六字段（含 `task_zh`、`inputs_given/missing`）+ 论文正文/附件 + 统一 JSON schema（含 `input_check`、`expected_data`）；
- **映射**：`hcc_report.py` 把 `population/timepoint/input/outcome/validation` 映射成 `visit.json`，并补 `tier`、`evidence_strength`、`papers`、`domain`；
- **兜底**：Agent 只回 Markdown 时置 `parse_mode=text_fallback`，黄条 + 原文 + 原始返回，不白屏；
- **Agent 选择**：名称大小写不敏感；`/api/agents` 能列出名称但找不到 `HCC-Fit` 时直接报错并列出可选名称，不再静默自动路由；
- **UI**：新增模式单选、单行“HCC 单次核查”流程、注意点、证据强度、原文引文展开、原始返回抽屉。

改动文件：`hcc_report.py`（新）、`planner.py`、`sciencepal.py`、`server.py`、`schemas/visit.schema.json`、`static/app.js`、`static/index.html`、`tools/test_hcc_report.py`（新）、`README.md`、`AGENT_HANDOFF.md`。

验证：

```
tools/test_hcc_report.py   8/8
selftest.py                8/8
tools/test_contract.py     8/8
tools/test_sp_heal.py      5/5
tools/test_sp_flow.py      10/10
合计 39/39
```

另外用本地假 Agent 跑通了一次完整链路（initiate → 轮询 → 解析 → 映射），`visit.schema.json` **0 处错误**。

**仍待真机确认**：SciencePal 上 HCC-Fit Agent 的实际返回是否就是 `population_check...` 这套；如果是别的 schema，把 `sp_raw/run_*.json` 的键名发我，我在 `normalize_report()` 里加分支即可。

---

*本检查只读文档与代码；第 10 节的实现改动见上表。*
