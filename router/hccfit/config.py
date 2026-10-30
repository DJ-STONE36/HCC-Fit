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
# 必须替换为真实的 filter agent UUID；空值会让 balanced 明确降级，而不是用占位符。
SCIENCEPAL_FILTER_AGENT_ID = os.environ.get("SCIENCEPAL_FILTER_AGENT_ID", "").strip()
