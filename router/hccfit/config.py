import os

# Package-specific: fast / balanced / auto only (deep disabled).
ENABLED_STRATEGIES = ["fast", "balanced", "auto"]
DEFAULT_STRATEGY = "auto"
DEFAULT_K = 5
DEFAULT_DEADLINE_MS = 120000
DEFAULT_REQUIRE_FULLTEXT = True

SCIENCEPAL_BASE_URL = os.environ.get("SCIENCEPAL_BASE_URL", "https://sciencepal.ai")
SCIENCEPAL_API_KEY = os.environ.get("SCIENCEPAL_API_KEY", "")
# 官方要求：模型 id 必须来自 /api/billing/available-models；留空 = 用平台默认模型。
SCIENCEPAL_MODEL = os.environ.get("SCIENCEPAL_MODEL", "").strip()
# 内置 filter agent（2026-10-30 实测可用的自定义 Agent「HCC-Fit」）。
# 环境变量优先；显式设空字符串（SCIENCEPAL_FILTER_AGENT_ID=）会关掉内置，改为自动发现/降级。
DEFAULT_FILTER_AGENT_ID = "61033129-3c47-4e72-885d-bcd4bb3e280a"
_env_filter_agent = os.environ.get("SCIENCEPAL_FILTER_AGENT_ID")
SCIENCEPAL_FILTER_AGENT_ID = (
    _env_filter_agent.strip() if _env_filter_agent is not None else DEFAULT_FILTER_AGENT_ID
)
