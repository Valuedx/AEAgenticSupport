"""AE AI Hub -- Orchestrator API Gateway."""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.config import settings
from app.api.workflows import router as workflows_router
from app.api.tools import router as tools_router
from app.security.rate_limiter import limiter

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="AE AI Hub - Orchestrator",
    description="Agentic workflow orchestration engine with visual DAG builder",
    version="0.5.0",
)

app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflows_router)
app.include_router(tools_router)


@app.get("/health")
def health():
    return {"status": "ok", "service": "ae-ai-hub-orchestrator"}


@app.get("/auth/token")
def dev_token(tenant_id: str = "default"):
    """Development-only endpoint to generate a JWT for testing.

    In production, tokens are issued by the organization's identity provider.
    """
    if settings.auth_mode != "dev":
        return JSONResponse(
            status_code=403,
            content={"detail": "Token generation disabled in production mode"},
        )
    from app.security.jwt_auth import create_access_token
    token = create_access_token(tenant_id=tenant_id)
    return {"access_token": token, "token_type": "bearer", "tenant_id": tenant_id}
