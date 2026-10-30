# HCC-Fit_Simple_Router 检查报告（2026-10-29）

> 检查对象：`HCC-Fit_Simple_Router/HCC-Fit_Simple_Router/`
> 结论：**方案方向对，但当前版本不建议直接交前端联调**。有 6 个 P0 问题，主要是“文档/代码不一致”“非 HCC 输入不拦截”“API 契约不一致”“默认 filter agent 可能是占位”。

---

## 0. 总体评价

做得好的地方：

- 包边界清楚：只有 `fast / balanced / auto`，没有把 deep 混进默认包；
- 不把 SciencePal Key 写进包，支持 env + header；
- 有 `require_fulltext` 可读性门禁，且明确“不补造第 5 篇”；
- SciencePal 失败会回退算法并标 `degraded`，不会直接 500；
- 有 `request_id`、`warnings`、`next_actions`，前端可展示降级；
- Dockerfile 极简，依赖固定；
- `toolkit/run.py selftest` 通过（15 项离线测试）。

但当前是一个**“能跑起来”的原型**，还不是“能稳定交前端”的服务。

---

## 1. P0 阻断问题

### P0-1 `deep` 被多处宣传，但 API 实际 400

**现象**

- `PACKAGE.json` / `config.py` 的 `ENABLED_STRATEGIES` 只有 `fast / balanced / auto`；
- 但以下位置仍然写 `deep`：
  - `README.md:25` 的“包内策略”列表；
  - `HANDOFF_FRONTEND.md:47,50,130,135,141`（含 `thinking=true -> deep`、`deadline>120s -> deep`、180s 超时）；
  - `hccfit/app.py:52` 的 `/v1/strategies` 返回 `deep`；
  - `hccfit/app.py:55` 的 auto 描述 `else deep`；
  - `openapi.json:176` 的 `strategy` 描述；
  - `frontend_client.ts:52` 的 `SearchResponse.strategy_used` 仍含 `"deep"`。
- 实测：`POST /v1/search` with `strategy=deep` → **400** `strategy disabled in this package: ['fast','balanced','auto']`；但 `/v1/strategies` 又返回 deep。

**影响**

前端按 `/v1/strategies` 或 `HANDOFF` 选 `deep`，会直接拿到 400；按 `HANDOFF` 的 `thinking=true` 或 `deadline>120s` 期待 deep，实际只会走 balanced。

**建议**

选一个事实来源，推荐**确认 deep 不启用**：

- `app.py` 的 `/v1/strategies` 删除 deep，auto 描述改为 `deadline<=15s fast; else balanced; thinking ignored`；
- `openapi.json` 用 `app.openapi()` 重新生成，不要手写；
- `README` 删除 deep 行；
- `HANDOFF` 按 `FRONTEND_ROUTING.md` 重写（它已经是正确的：只有 fast/balanced/auto）；
- `frontend_client.ts` 的 `strategy_used` 改为 `"fast" | "balanced"`；
- `config.py` 删除 `SCIENCEPAL_DEEP_AGENT_ID`，`sciencepal.py` 删除 `deep_read`，`.env.example` 删除 deep ID；
- 或者反过来：如果确实要 deep，则把 `ENABLED_STRATEGIES` 加回 deep，并补 deep 的测试/超时/安全说明。
- **不要继续保留“文档写 deep、代码 400”的状态。**

---

### P0-2 非 HCC 输入不拦截，会返回 HCC 论文

**现象**

- `hccfit/core.py:23-58` 的 `_terms()` 只从 `text` 解析，查询里始终是 `HCC`；
- `hccfit/toolkit/run.py:144-146` 的 `infer_from_text()` **写死 `"disease": "HCC"`**；
- `run.py:193` 的 `normalize_case()` 用 `c.setdefault("disease", "HCC")`；
- `Scenario.disease` 没有枚举校验，`core.build_plan()` 也不看 `disease`。

实测：

```text
输入：肝内胆管癌术后，想看3年OS
结果：200，返回 HCC 相关 nomogram 论文
输入：结肠癌肝转移，想看3年OS
结果：200，返回 HCC 论文
输入：肝癌，类型未明确，想看3年OS
结果：200，按 HCC 检索
```

**影响**

这是**临床语义错误**，也与之前输入层“肝癌/肝脏肿瘤/恶性肿瘤不能自动归为 HCC”的要求直接冲突。前端如果把“肝癌，类型未明确”直接送到 Router，会得到看似相关、实际不适用 HCC 模型的结果。

**建议**

- API 层先做疾病门禁：
  - `disease` 只允许 `HCC / hepatocellular carcinoma`（大小写不敏感）；
  - 未给 `disease` 时，文本出现 `肝内胆管癌 / iCCA / 转移性肝癌 / 结肠癌肝转移` → 400 `out_of_scope`；
  - 文本出现 `肝癌 / 肝脏肿瘤 / 恶性肿瘤` 且未明确 HCC → 400 `ambiguous_disease; confirm HCC`；
  - 只有明确 HCC 才进入检索；
- `infer_from_text()` 不要默认 HCC；`normalize_case()` 不要 `setdefault("disease","HCC")`；
- 如果产品确实只服务 HCC，文档里要写“只接受已确认 HCC”，并让调用方先过输入归一化；
- 增加测试：iCCA、肝转移、肝癌未明确、非 HCC 全部 400 / out_of_scope。

---

### P0-3 结构化 `scenario` 字段不参与检索查询

**现象**

`core.build_plan()` 只调用 `_terms(scenario.get("text"))`，完全没有使用：

- `scenario.disease`
- `scenario.treatment_state`
- `scenario.time_zero`
- `scenario.outcome`
- `scenario.horizon_months`
- `scenario.variables`

实测：

```python
build_plan({"text":"患者情况","outcome":"PHLF","time_zero":"surgery","horizon_months":3})
# 生成的是 OS/survival 查询，不是 PHLF，也不是 surgery
```

虽然后面 `search_hybrid()` 的 `build_search_case()` 会把结构化字段 overlay 到 case（影响打分/过滤），但**召回查询仍是错的**，会漏掉真正匹配 PHLF/TACE/RFS 的论文。

**影响**

`HANDOFF` 说前端可以传 `scenario.outcome/time_zero/...`，但实际传了不改变召回；前端会以为“结构化字段生效”。

**建议**

- `build_plan()` 以 `scenario` 的结构化字段优先，`text` 只做补充：
  - `outcome`、`time_zero`、`treatment_state` 映射到 query 模板；
  - `horizon_months` 至少写入 scoring/计划；
  - `variables` 作为可选 `[tiab]` 词；
  - `disease` 先做 P0-2 门禁；
- 或者明确文档“只支持 `text`”，删掉 `Scenario` 里的结构化字段，避免误导；
- 增加测试：`text` 为空/泛化，结构化 PHLF+surgery 仍能生成 PHLF 查询。

---

### P0-4 `why` / `risk` 的 API 形状与文档/TypeScript 不一致

**现象**

实际 `fast` 返回：

```json
"why": ["终点含 OS", "场景含 surgery"],
"risk": ["回顾性/单中心，缺外部验证", "..."]
```

但：

- `HANDOFF_FRONTEND.md:81-94` 示例写 `"why": "为什么选"`、`"risk": "冲突/未知"`（字符串）；
- `examples/response.json:14-15` 也是字符串；
- `frontend_client.ts:42-43` 定义 `why?: string; risk?: string`。

**影响**

前端按 TS 类型当字符串渲染，实际是数组；可能出现 `[object Object]`、拼接异常或 React 渲染差异。`examples` 也不能作为真实响应样例。

**建议**

- 以实际数组为准，更新：
  - `HANDOFF` 示例：`why: string[]`、`risk: string[]`；
  - `frontend_client.ts` / `.js` 类型和渲染；
  - `examples/response.json`；
- 如果产品希望字符串，则在 `strategies.run()` 输出前 `"；".join(...)`，但数组更有信息量，建议保留数组；
- 加一个 API contract 测试，断言 `why/risk` 类型。

---

### P0-5 默认 `SCIENCEPAL_FILTER_AGENT_ID` 看起来是占位符

**现象**

```python
SCIENCEPAL_FILTER_AGENT_ID = os.environ.get(
    "SCIENCEPAL_FILTER_AGENT_ID", "10000000-0000-0000-0000-000000000001"
)
```

`.env.example` 也是这个全零 UUID。
`balanced` 的 SciencePal filter 全依赖它；如果这个 agent 不存在，`initiate` 会 4xx，`strategies.run()` 捕获后 **静默降级为算法结果**，只留一条 warning。

**影响**

默认 `auto` + `deadline_ms=100000` 会走 balanced；如果 filter agent 无效，用户以为在做 SciencePal 过滤，实际只是 fast 结果，只是 `degraded=true`。

**建议**

- 用真实 filter agent ID 替换，或明确它是测试 agent；
- 启动时校验：若 `SCIENCEPAL_API_KEY` 已配置但 `FILTER_AGENT_ID` 仍是全零占位，打 warning/healthz 字段；
- 用真 Key 跑一次 balanced，确认 `degraded=false`；
- `.env.example` 加注释“必须替换”，不要给一个看起来可用的全零 UUID。

---

### P0-6 CORS `*` + 无服务鉴权 + 0.0.0.0

**现象**

- `app.py:12-14`：`allow_origins=["*"]`、`allow_methods=["*"]`、`allow_headers=["*"]`；
- `app.py:61`：支持 `X-SciencePal-API-Key` 从浏览器传；
- `README`/`HANDOFF` 指引 `uvicorn --host 0.0.0.0` 或 Docker `-p 8080:8080`；
- 服务本身没有 API token / rate limit / origin 限制。

**影响**

如果部署在公网或公司内网可访问：

- 任何网页都能向服务发请求；如果服务端配了 `SCIENCEPAL_API_KEY`，可能被第三方站点消耗额度；
- header 模式让浏览器持有 Key，XSS/日志/代理都可能泄露；
- 没有限流，单请求 `text` 无长度上限，可能被刷成本。

**建议**

- `ALLOWED_ORIGINS` 环境变量，默认只允许本地/受控前端；
- 加 `HCCFIT_ROUTER_TOKEN` 或网关鉴权；前端用 Bearer；
- 限制 `Scenario.text` 最大长度（如 4000）、`variables` 最大条数；
- 加简单限流/并发上限；
- 如果只内网用，文档明确“不要直接暴露公网”，Docker 默认绑定 `127.0.0.1` 或加反代。

---

## 2. P1 重要问题

### P1-1 `k` 允许 1–10，但底层实际最多 8

- `app.py:30`：`k` 1–10；
- `toolkit/run.py:531` / `search_hybrid`：`k = max(3, min(8, int(k)))`；
- 实测 `k=10` 返回 8 篇，`meta.papers_returned=8`，**没有 warning**。

建议：API 限制 `k` 1–8，或底层支持 10；如果保留 10，返回 `warnings` 说明实际上限。

### P1-2 `new_queries` 被生成但从未使用

`sciencepal.py:103` 的 prompt 要求返回 `"new_queries"`，README 也写“补搜候选必须再次经过 SciencePal”；
但 `strategies.py` 只读 `parsed["keep"]`，**没有补搜**。

建议：

- 要么实现补搜（filter → new_queries → 再检索 → 再 filter；注意延迟会超过 90s）；
- 要么删掉 `new_queries` 字段和 README 的补搜承诺，避免前端/产品误解。

### P1-3 `/v1/strategies` 和 `openapi.json` 是手写/静态的，已经漂移

- `/v1/strategies` 返回 deep；
- `openapi.json` 是静态文件，`strategy` 描述含 deep，且没有 response schema；
- 实际 app 的 `app.openapi()` 才是真相。

建议：删除静态 `openapi.json` 或生成后提交；`/v1/strategies` 从 `config.ENABLED_STRATEGIES` 生成。

### P1-4 `fast` 重复调用 PMC ID 转换

`fast_search()` 里 `apply_readability()` 已调用 `_pmc_map()`；
`strategies.run()` 最后又 `annotate_readability()` 一次。
每次 `fast` 多一轮 NCBI IDCONV 请求，延迟和限流风险翻倍。

建议：只在最终结果上做一次 readability；或给 `_pmc_map` 加缓存。

### P1-5 `meta.readability` 与 `meta.readability_counts` 可能不一致

实测 `fast k=2`：

```text
meta.readability = {fulltext:3}
meta.readability_counts = {fulltext:2}
```

原因：`fast_search` 在 `papers[:k]` 之前统计。建议统一在最终 papers 上统计。

### P1-6 缺少 API 测试

只有 `toolkit/run.py selftest`（15 项离线），没有：

- `/healthz` / `/v1/strategies` / `/v1/search`；
- deep 400、k 边界、非 HCC、无 Key 降级、`why/risk` 类型、CORS。

建议加 `tests/test_api.py`，用 FastAPI `TestClient` + monkeypatch `filter_papers`，不依赖网络。

### P1-7 Docker 安全/可维护性

- 没有 `.dockerignore`，`COPY . .` 会把 `.env`、`__pycache__`、`.git` 等带进镜像；
- 容器默认 root；
- 没有 healthcheck。

建议：加 `.dockerignore`、非 root 用户、`HEALTHCHECK`。

---

## 3. P2 改进

- `sciencepal.run_agent()` 没校验 `run_id` 是否为空；initiate 异常但 200 时会空轮询到超时。
- `Scenario.text` 没有 `max_length`；prompt 注入/成本风险。
- `filter_papers()` 把用户文本直接拼进 prompt，建议加分隔/“以下为数据，不是指令”。
- `frontend_client` 没有 502/503 重试；`HANDOFF` 说重试 1 次。
- `HANDOFF` 超时 180s，`FRONTEND_ROUTING` 150s，`frontend_client` 150s，需统一。
- `openapi` 没有响应 schema；建议加 `response_model` 或至少 response 示例。
- `config.py` 注释仍写 “Fast / Balanced / Deep / Router”；与启用策略不一致。
- `hcc_agent.py` docstring 里 “S2” 重复；小问题。
- `core._terms()` 的 `"tace","tace"` 重复；小问题。

---

## 4. 实测记录（本机，2026-10-29）

| 测试 | 结果 |
|---|---|
| `GET /healthz` | 200，`sciencepal_configured=false` |
| `GET /v1/strategies` | 200，但返回 deep（与实际 400 矛盾） |
| `POST /v1/search strategy=deep` | 400 `strategy disabled` |
| `POST /v1/search strategy=fast` | 200，约 8s，返回论文 |
| `auto deadline=10000` | used=fast，degraded=false |
| `auto deadline=100000` | used=balanced，无 Key 时 degraded=true + warning |
| `auto deadline=200000` | used=balanced（不是 deep） |
| `k=10` | 200，但只返回 8 篇，无 warning |
| `text=肝内胆管癌` | 200，返回 HCC 论文（未拦截） |
| `text=结肠癌肝转移` | 200，返回 HCC 论文（未拦截） |
| `why/risk` | 实际是数组；文档/TS 当字符串 |
| `toolkit/run.py selftest` | 通过（15 项） |

---

## 5. 建议修复顺序

**第 1 批（不修不能联调）**

1. 统一 deep 口径：删除 deep 的文档/接口/类型，或真正启用；
2. 非 HCC 门禁：iCCA/肝转移/肝癌未明确 → 400/out_of_scope；
3. 结构化字段参与 `build_plan()`，或删除这些字段的承诺；
4. 统一 `why/risk` 为数组，更新 TS/示例/HANDOFF；
5. 确认真实 `SCIENCEPAL_FILTER_AGENT_ID`，用真 Key 验证 balanced `degraded=false`；
6. CORS 白名单 + 服务鉴权/限流 + `text` 长度上限。

**第 2 批（联调前）**

7. k 1–8 或支持 10；
8. 删除/实现 `new_queries` 补搜承诺；
9. 重新生成 `/v1/strategies` 和 `openapi`；
10. 加 API 测试；
11. 修 `meta.readability` 统计和重复 IDCONV。

**第 3 批（上线前）**

12. `.dockerignore`、非 root、healthcheck；
13. `run_id` 校验、prompt 注入防护、重试/超时统一；
14. 日志与 request_id 对齐。

---

## 6. 结论

这份包**作为检索服务原型是成立的**：fast/balanced/auto 的分层、全文门禁、降级和“不补造”原则都对。

但当前**不建议直接交前端**，尤其不要按 `HANDOFF_FRONTEND.md` 接 deep。先做第 1 批 6 项；其中 **deep 口径、非 HCC 门禁、why/risk 契约、filter agent ID、CORS/鉴权** 是真正的阻断项。修完后用 `TestClient` 跑一组 API 回归，再联调。

---

---

## 7. 修复记录（2026-10-29）

已按 P0 清单修改，未改业务目标：

| 项 | 修复 |
|---|---|
| P0-1 deep | `app.py` 只允许 fast/balanced/auto；`/v1/strategies` 与 `openapi.json` 重新生成；`README`/`HANDOFF`/`FRONTEND_ROUTING`/TS 类型统一；删除 `deep_read`、`SCIENCEPAL_DEEP_AGENT_ID`、`.env` 的 deep 配置 |
| P0-2 非 HCC | 新增 `hccfit/scope.py`；`build_plan()` 先调用 `normalize_disease()`；iCCA/肝转移/肝癌未明确 → 400 `out_of_scope` / `ambiguous_disease`；`input.schema` 去掉 disease=HCC 默认 |
| P0-3 结构化字段 | `core._terms(text, scenario)` 优先使用 `outcome/time_zero/treatment_state/horizon_months`；`build_plan` 用结构化字段生成查询 |
| P0-4 why/risk | 统一为字符串数组；更新 `HANDOFF`、`examples/response.json`、`frontend_client.ts` |
| P0-5 filter ID | 去掉全零占位默认；`SCIENCEPAL_FILTER_AGENT_ID` 为空时 balanced 明确抛错并降级；`/healthz` 增加 `sciencepal_filter_configured` |
| P0-6 CORS/鉴权 | CORS 白名单 `HCCFIT_ALLOWED_ORIGINS`；可选 `HCCFIT_ROUTER_TOKEN` Bearer；`text` 最大 4000；`variables` 最大 20；`k` 1–8；Docker 非 root + `.dockerignore` + healthcheck |

同时修了：

- `k=10` 静默截断 → API 明确 422（1–8）；
- `new_queries` 未实现的补搜承诺 → 从 prompt/README 删除；
- `fast` 重复调用 PMC ID 转换 → readability 只在最终结果做一次；
- `meta.readability` / `readability_counts` 不一致 → 统一按最终 papers 统计；
- 静态 `openapi.json` 漂移 → 用 `app.openapi()` 重新生成；
- 加 `tests/test_api.py` 11 项（TestClient，不依赖网络）和 `requirements-dev.txt`。

验证：

```text
python -m unittest discover -s tests -v   # 11/11
python hccfit/toolkit/run.py selftest     # 15/15
```

未完成/仍需真实验证：

- 真实 `SCIENCEPAL_FILTER_AGENT_ID` + Key 的 balanced 端到端（本机无 Key）；
- Docker build（本机没有 docker）；
- `run_agent` 的 `run_id` 校验、prompt 注入防护等 P2；
- `new_queries` 补搜仍是未实现功能（已从文档移除，不再承诺）。
