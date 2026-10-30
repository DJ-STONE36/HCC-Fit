# HCC-Fit Strategy Service

> 本包只有 **fast / balanced / auto**；`deep` 不启用，传 `deep` 会 400。
> 服务只接受**已确认 HCC**；肝内胆管癌、肝转移、“肝癌类型未明确”会被 400 拒绝。

算法检索 + SciencePal filter 的可部署 HTTP 服务。

## 快速启动

```bash
python -m pip install -r requirements.txt

export SCIENCEPAL_API_KEY=sp_live_xxx
export SCIENCEPAL_BASE_URL=https://sciencepal.ai
export SCIENCEPAL_MODEL=platform::deepseek-v4-flash
# 必须：真实 filter agent UUID；留空则 balanced 明确降级为 fast
export SCIENCEPAL_FILTER_AGENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx

# 可选：服务鉴权（前端带 Authorization: Bearer ...）
export HCCFIT_ROUTER_TOKEN=
# 可选：CORS 白名单
export HCCFIT_ALLOWED_ORIGINS=http://localhost:3000,http://127.0.0.1:3000

uvicorn hccfit.app:app --host 127.0.0.1 --port 8080
```

Docker：

```bash
docker build -t hccfit-strategy .
docker run -p 8080:8080 \
  -e SCIENCEPAL_API_KEY=sp_live_xxx \
  -e SCIENCEPAL_FILTER_AGENT_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
  -e HCCFIT_ROUTER_TOKEN=change-me \
  hccfit-strategy
```

**不要直接暴露公网**；如果必须，放在网关后并启用 `HCCFIT_ROUTER_TOKEN`。

## 测试

```bash
pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
```

## 接口

- `GET /healthz`
- `GET /v1/strategies`（只返回 fast/balanced/auto）
- `POST /v1/search`（字段见 `HANDOFF_FRONTEND.md`）

`k` 范围 **1–8**；超过 8 会 422，不静默截断。

## 疾病范围

只接受：

- `scenario.disease = HCC / hepatocellular carcinoma`；或
- 原话明确写 `HCC / 肝细胞癌 / hepatocellular carcinoma`。

以下会 400：

- `肝内胆管癌 / iCCA / 胆管癌` → `out_of_scope`
- `结肠癌肝转移 / 肝转移` → `out_of_scope`
- `肝癌 / 肝脏肿瘤 / 恶性肿瘤` 未确认 HCC → `ambiguous_disease`

前端应先在输入层让医生确认 HCC，再调用本服务。

## 包内策略

- `fast`：纯算法，5–10 s；
- `balanced`：算法 + SciencePal filter，60–90 s；
- `auto`：`deadline_ms <= 15s` → fast；否则 balanced；`thinking` 忽略。

过滤后不强行补满 k 篇；SciencePal 失败会回退算法并置 `meta.degraded=true` 且返回 warning。

## 可读性

每篇返回 `readability.level`：

- `fulltext`：PMC 全文；
- `abstract_only`：仅摘要；
- `metadata_only`：仅题录。

`require_fulltext=true` 时只返回 `fulltext`，数量不足不补造。**默认 true**。

## 响应契约

- `papers[].why` 和 `papers[].risk` 是 **字符串数组**；
- `meta.strategy_used` 只会是 `fast` 或 `balanced`；
- `strategy_requested=auto` 时用 `meta.strategy_used` 判断实际策略。

## 嵌入桌面版（进程内调用，2026-10-30 起默认）

桌面版不再要求单独启动本服务：`desktop/app/router_engine.py` 直接

```python
import hccfit.strategies as strategies
strategies.run(scenario, strategy="fast", k=8, deadline_ms=150000, ...)
```

- 开发态：桌面找包内同级 `router/`（`HCCFIT_ROUTER_DIR` 可覆盖）；
- 打包态：`desktop/HCC-Fit.spec` 把 `hccfit` + `toolkit`（`run/validate/hcc_agent/multi_search/paper_search/sources`）
  一起冻进 exe，依赖（`requests`、`xml.etree` 等）由 PyInstaller 静态分析收集；
- 因此 **本目录是唯一源码**，桌面不复制第二份；
- 多端/远程部署时仍可跑本服务（HTTP `/v1/search`），桌面在弹窗里切“外部服务”或设 `HCCFIT_ROUTER_URL`。

## 安全

- 不在前端暴露 `SCIENCEPAL_API_KEY`；
- 所有请求经后端；header 模式只适合受控内网；
- 服务端启用了 `HCCFIT_ROUTER_TOKEN` 后，`/v1/search` 必须带 `Authorization: Bearer <token>`；
- CORS 默认只允许 localhost；生产用 `HCCFIT_ALLOWED_ORIGINS` 收紧；
- 临床输入必须脱敏/模拟；输出不是个体预测，不替代临床决策。
