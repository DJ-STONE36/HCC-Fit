# 前端路由说明（HCC-Fit_Simple_Router）

## 只有两种实际模式

| strategy | 算法 | LLM | 延迟 | 行为 |
|---|---|---|---:|---|
| `fast` | 无权重 hybrid | 无 | 5–10 s | 纯算法 Top-5 |
| `balanced` | 无权重 hybrid | SciencePal filter | 60–90 s | Top-10 → SP filter → 最多 k 篇 |
| `auto` | 取决于 deadline | 取决于路由 | 5–100 s | deadline ≤ 15 s → fast；否则 balanced |

**没有 deep。** `thinking=true` 会被忽略；不要传 `strategy=deep`，会 400。

## 前端怎么填

```ts
const strategy = fastMode ? "fast" : "balanced";
// 或者交给后端：
// strategy="auto", deadline_ms=10000 -> fast; 100000 -> balanced
```

请求：

```json
{
  "scenario": {"text": "..."},
  "strategy": "auto",
  "k": 5,
  "deadline_ms": 100000,
  "thinking": false,
  "require_fulltext": true,
  "request_id": "req-001"
}
```

- `fast`：无 SciencePal，只用于速度/降级；
- `balanced`：有 SciencePal filter，推荐默认；
- `auto`：前端不想判断时用；只会在 fast/balanced 之间选；
- `k` 范围 1–8；9 以上 422。

## 前端必须处理

- `meta.strategy_used`：实际走了哪个（只会是 `fast` / `balanced`）；
- `papers[].why` / `papers[].risk`：是**字符串数组**，不是字符串；
- `warnings`：少于 k 篇、全文被剔除、SP 不可用降级；
- `meta.degraded=true`：SP 失败回退算法，UI 要标“降级”；
- `papers[].readability.level`：不是 `fulltext` 的不进精读页；
- 非 HCC / 未确认 HCC：400，前端要提示“先确认 HCC”，不要重试。

## 超时

- fast：60 s；
- balanced：120 s；
- 建议统一 150 s。

## 服务鉴权

如果后端启用了 `HCCFIT_ROUTER_TOKEN`，请求必须带：

```http
Authorization: Bearer <router-token>
```

`/healthz` 的 `auth_required` 可判断是否启用。CORS 默认只允许 localhost；生产用 `HCCFIT_ALLOWED_ORIGINS` 配置。

## 不允许

- 不要传 `deep`；
- 不要在前端暴露 `SCIENCEPAL_API_KEY`；
- 不要自动补造第 5 篇；
- 不要把 `fast` 结果标成“SciencePal 核查”。
