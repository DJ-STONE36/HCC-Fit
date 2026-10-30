import os

# Package-specific: fast / balanced / auto only (deep disabled).
ENABLED_STRATEGIES = ["fast", "balanced", "auto"]
DEFAULT_STRATEGY = "auto"
DEFAULT_K = 5
DEFAULT_DEADLINE_MS = 120000
DEFAULT_REQUIRE_FULLTEXT = True

SCIENCEPAL_BASE_URL = os.environ.get("SCIENCEPAL_BASE_URL", "https://sciencepal.ai")
SCIENCEPAL_API_KEY = os.environ.get("SCIENCEPAL_API_KEY", "")
SCIENCEPAL_MODEL = os.environ.get("SCIENCEPAL_MODEL", "platform::deepseek-v4-flash")
# 必须替换为真实的 filter agent UUID；空值会让 balanced 明确降级，而不是用占位符。
SCIENCEPAL_FILTER_AGENT_ID = os.environ.get("SCIENCEPAL_FILTER_AGENT_ID", "").strip()
