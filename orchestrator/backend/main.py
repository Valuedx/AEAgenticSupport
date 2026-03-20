"""AE AI Hub -- Orchestrator API Gateway."""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.api.workflows import router as workflows_router
from app.api.tools import router as tools_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

app = FastAPI(
    title="AE AI Hub - Orchestrator",
    description="Agentic workflow orchestration engine with visual DAG builder",
    version="0.1.0",
)

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
