"""FastAPI service for HCC-Fit strategies."""
from __future__ import annotations

import os

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import config, strategies
from .scope import ScopeError

ALLOWED_ORIGINS = [
    x.strip() for x in os.environ.get(
        "HCCFIT_ALLOWED_ORIGINS",
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://localhost:8080,http://127.0.0.1:8080",
    ).split(",") if x.strip()
]
ROUTER_TOKEN = os.environ.get("HCCFIT_ROUTER_TOKEN", "").strip()

app = FastAPI(title="HCC-Fit Strategy API", version="0.1.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization", "X-SciencePal-API-Key"],
    allow_credentials=False,
)


def require_router_token(authorization: str | None = Header(default=None)):
    """可选服务鉴权：配置 HCCFIT_ROUTER_TOKEN 后，/v1/search 必须带 Bearer。"""
    if not ROUTER_TOKEN:
        return
    if authorization != "Bearer " + ROUTER_TOKEN:
        raise HTTPException(status_code=401, detail="invalid or missing router token")


class Scenario(BaseModel):
    text: str = Field(..., min_length=1, max_length=4000,
                      description="自由文本临床场景；只接受已确认 HCC")
    disease: str | None = Field(None, description="HCC / hepatocellular carcinoma；其它值会被拒绝")
    treatment_state: str | None = None
    time_zero: str | None = None
    outcome: str | None = None
    horizon_months: int | None = Field(None, ge=0, le=120)
    variables: list[str] = Field(default_factory=list, max_length=20)


class SearchRequest(BaseModel):
    scenario: Scenario
    strategy: str = Field("auto", description="fast | balanced | auto")
    k: int = Field(5, ge=1, le=8, description="1–8；超过 8 会被拒绝，不做静默截断")
    deadline_ms: int = Field(120000, ge=5000, le=600000)
    thinking: bool = Field(False, description="保留字段；本包忽略（deep 未启用）")
    require_fulltext: bool = Field(True, description="true 时只返回有 PMC 全文的论文")
    request_id: str | None = Field(None, max_length=100)


@app.get("/healthz")
def health():
    return {
        "ok": True,
        "service": "hccfit-strategy",
        "version": "0.1.1",
        "sciencepal_configured": bool(config.SCIENCEPAL_API_KEY),
        "sciencepal_filter_configured": bool(config.SCIENCEPAL_FILTER_AGENT_ID),
        "sciencepal_header_supported": True,
        "auth_required": bool(ROUTER_TOKEN),
        "enabled_strategies": list(config.ENABLED_STRATEGIES),
    }


@app.get("/v1/strategies")
def list_strategies():
    catalog = {
        "fast": {"id": "fast", "latency": "5-10s", "llm": False,
                 "use": "speed; no SciencePal; no conflict guarantee"},
        "balanced": {"id": "balanced", "latency": "60-90s", "llm": "SciencePal filter",
                     "use": "default; filter pool to top-k; no unverified padding"},
        "auto": {"id": "auto", "latency": "5-100s", "llm": "depends",
                 "use": "deadline_ms<=15s fast; else balanced; thinking ignored"},
    }
    return {
        "default": config.DEFAULT_STRATEGY,
        "strategies": [catalog[x] for x in config.ENABLED_STRATEGIES if x in catalog],
    }


@app.post("/v1/search", dependencies=[Depends(require_router_token)])
def search(req: SearchRequest,
           x_sciencepal_api_key: str | None = Header(default=None,
                                                     alias="X-SciencePal-API-Key")):
    if req.strategy not in config.ENABLED_STRATEGIES:
        raise HTTPException(
            status_code=400,
            detail=f"strategy must be one of {list(config.ENABLED_STRATEGIES)}; "
                   f"deep is not enabled in this package",
        )
    try:
        return strategies.run(req.scenario.model_dump(), req.strategy, req.k,
                              req.deadline_ms, req.thinking, req.require_fulltext,
                              req.request_id, api_key=x_sciencepal_api_key)
    except ScopeError as exc:
        raise HTTPException(status_code=400, detail={"code": exc.code, "message": exc.message})
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8080)
