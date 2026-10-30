# 前端 Agent 交接说明（HCC-Fit_Simple_Router）

> **本包只有 fast / balanced / auto；deep 不启用，传 deep 会 400。**
> 服务只接受**已确认 HCC**；肝内胆管癌、肝转移、“肝癌类型未明确”会 400。

前端只调用 **一个** `POST /v1/search`，不要直接调 PubMed 或 SciencePal。

## 1. 部署

```bash
python -m pip install -r requirements.txt

export SCIENCEPAL_API_KEY=sp_live_xxx
export SCIENCEPAL_BASE_URL=https://sciencepal.ai
export SCIENCEPAL_MODEL=platform::deepseek-v4-flash
export SCIENCEPAL_FILTER_AGENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# 可选
export HCCFIT_ROUTER_TOKEN=change-me
export HCCFIT_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

uvicorn hccfit.app:app --host 127.0.0.1 --port 8080
```

`SCIENCEPAL_FILTER_AGENT_ID` 必须是真实 filter agent UUID；留空时 balanced 会明确降级为 fast，并在 `warnings` 中说明。

## 2. 健康检查

```bash
curl http://localhost:8080/healthz
```

返回包含：

- `sciencepal_configured`：是否配置 Key；
- `sciencepal_filter_configured`：是否配置 filter agent；
- `auth_required`：是否启用 `HCCFIT_ROUTER_TOKEN`；
- `enabled_strategies`：`["fast","balanced","auto"]`。

## 3. 请求

```http
POST /v1/search
Content-Type: application/json
Authorization: Bearer <router-token>   # 启用 HCCFIT_ROUTER_TOKEN 时必填
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `scenario.text` | string | 是 | 自由文本；最大 4000 字；必须能确认 HCC |
| `scenario.disease` | string | 否 | `HCC` / `hepatocellular carcinoma`；其它值 400 |
| `scenario.treatment_state` | string | 否 | `curative resection` / `TACE` / `HAIC` / `unresectable systemic` / `transplant` |
| `scenario.time_zero` | string | 否 | `surgery` / `tace` / `systemic` / `transplant` |
| `scenario.outcome` | string | 否 | `OS` / `RFS` / `PFS` / `PHLF` / `response` / `LOS` |
| `scenario.horizon_months` | int | 否 | 0–120 |
| `scenario.variables` | string[] | 否 | 最多 20 个 |
| `strategy` | string | 否 | `fast` / `balanced` / `auto` |
| `k` | int | 否 | **1–8**；9 以上 422 |
| `deadline_ms` | int | 否 | 5000–600000；auto 用它路由 |
| `thinking` | bool | 否 | **忽略**（deep 未启用） |
| `require_fulltext` | bool | 否 | 默认 true；只返回 PMC 全文 |
| `request_id` | string | 否 | 前端追踪 id，原样回传 |

示例：

```json
{
  "scenario": {
    "text": "男，58岁，乙肝，HCC，已行根治性切除，术后 MVI 阳性、肿瘤 3.2cm；想看 3 年总生存。",
    "disease": "HCC",
    "treatment_state": "curative resection",
    "time_zero": "surgery",
    "outcome": "OS",
    "horizon_months": 36
  },
  "strategy": "auto",
  "k": 5,
  "deadline_ms": 100000,
  "thinking": false,
  "require_fulltext": true,
  "request_id": "req-demo-001"
}
```

**非 HCC 会返回 400：**

```json
{"detail":{"code":"out_of_scope","message":"本服务只支持 HCC；..."}}
```

或：

```json
{"detail":{"code":"ambiguous_disease","message":"原话只写了“肝癌/肝脏肿瘤/恶性肿瘤”，未确认 HCC；..."}}
```

## 4. 响应

```json
{
  "request_id": "req-demo-001",
  "strategy_requested": "auto",
  "strategy_used": "balanced",
  "scenario": {},
  "papers": [
    {
      "pmid": "40052130",
      "doi": "10.3389/fonc.2025.1559083",
      "title": "...",
      "journal": "Frontiers in Oncology",
      "year": 2025,
      "why": ["终点含 OS", "场景含 surgery"],
      "risk": ["回顾性/单中心，缺外部验证"],
      "sp_fit": 2,
      "sp_reason": "SciencePal 的 fit 理由",
      "readability": {
        "level": "fulltext",
        "pmcid": "PMC11882412",
        "url": "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC11882412/",
        "full_text": true
      }
    }
  ],
  "meta": {
    "strategy_used": "balanced",
    "latency_ms": 71234,
    "retrieval_ms": 5200,
    "llm_ms": 65000,
    "filter_in": 10,
    "filter_out": 5,
    "papers_returned": 5,
    "readability_counts": {"fulltext": 5, "abstract_only": 0, "metadata_only": 0},
    "degraded": false
  },
  "warnings": [],
  "next_actions": ["open_full_text", "verify_model_variables", "ask_missing_parameters"]
}
```

前端必须处理：

- `papers[].why` / `papers[].risk` 是 **字符串数组**，不是字符串；
- `warnings` 非空时展示黄色提示，不要静默；
- `meta.degraded=true` 时说明“SciencePal 不可用，已回退算法结果”；
- `readability.level`：
  - `fulltext`：可打开 PMC 全文；
  - `abstract_only`：只能读摘要，显示“仅摘要”；
  - `metadata_only`：只有题录，不作为精读候选；
- `require_fulltext=true` 时返回数量可能 < k，不允许自动补造；
- `strategy_used` 只会是 `fast` / `balanced`。

## 5. 策略与路由

| strategy | 延迟 | 用法 |
|---|---:|---|
| `fast` | 5–10 s | 纯算法，无 SciencePal；快速浏览/降级 |
| `balanced` | 60–90 s | 算法 + SciencePal filter；默认 |
| `auto` | 5–100 s | `deadline_ms <= 15s` → fast；否则 balanced |

- **没有 deep**；`thinking` 被忽略；
- 页面首次搜索：`auto` + `deadline_ms=100000`；
- 弱网/快速浏览：`fast`；
- 需要“只给能精读的”：`require_fulltext=true`。

## 6. 超时/重试/降级

- 前端 HTTP timeout 建议 **150 s**；
- 502/503：重试 1 次，退 `fast`；
- 400：请求字段/疾病范围错误，不要重试；
- 401：服务鉴权失败，检查 `Authorization`；
- `meta.degraded=true`：展示降级提示，不要把它当 SciencePal 结果；
- `request_id` 原样回传。

## 7. 错误码

| HTTP | 含义 |
|---|---|
| 400 | strategy/k 不合法；非 HCC / 未确认 HCC |
| 401 | 服务 token 缺失/错误 |
| 422 | 请求体校验失败（如 k=9、text 为空） |
| 500 | 服务内部错误 |
| 502/503 | 网关/SciencePal 不可用（若前置网关） |

## 8. 安全

- 不要在前端暴露 `SCIENCEPAL_API_KEY`；
- 生产建议由后端持有 env key；header 模式只适合受控内网；
- 启用 `HCCFIT_ROUTER_TOKEN` 后，前端必须带 `Authorization: Bearer <token>`；
- CORS 默认只允许 localhost；用 `HCCFIT_ALLOWED_ORIGINS` 收紧；
- 临床输入必须脱敏/模拟；
- 输出是文献适配信息，不是个体预测，不替代临床决策。

## 9. 前端调用代码

- TypeScript：`frontend_client.ts`
- JavaScript：`frontend_client.js`

```ts
import { searchHCC } from "./frontend_client";

const res = await searchHCC(
  { scenario: { text: userInput, disease: "HCC" }, strategy: "auto", deadline_ms: 100000 },
  {
    baseUrl: "http://localhost:8080",
    apiKey: sciencePalKey,       // 可省略，交给后端 env
    routerToken: routerToken,    // 启用 HCCFIT_ROUTER_TOKEN 时必填
  }
);
```

`apiKey` 省略时，后端必须配置 `SCIENCEPAL_API_KEY`。
