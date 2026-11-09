from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api.routes import router
from app.core.config import settings

app = FastAPI(
    title=settings.PROJECT_NAME, version="3.9.13-b1.1", openapi_url="/api/v1/openapi.json"
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)
app.include_router(router, prefix=settings.API_V1_PREFIX)
from app.api.comparisons import router as comparisons_router
app.include_router(comparisons_router, prefix=settings.API_V1_PREFIX)


@app.get("/health/live")
def live():
    return {"status": "ok"}


@app.get("/")
def root():
    return {
        "service": "StockExpert API",
        "version": "3.9.13-b1.1",
        "docs": "/docs",
        "frontend": "http://localhost:5173",
    }


from fastapi import Depends
from fastapi.responses import JSONResponse
from app.api.deps import get_current_user


@app.get("/health/ready")
def ready(user=Depends(get_current_user)):
    from redis import Redis
    from sqlalchemy import text
    from app.db import engine
    from app.services.storage import client as storage_client
    from app.services.search import client as search_client
    from app.services.model_registry import default_config, probe_ollama, availability

    checks = {}
    try:
        with engine.connect() as c:
            c.execute(text("select 1"))
        checks["postgres"] = True
    except Exception:
        checks["postgres"] = False
    try:
        checks["redis"] = bool(
            Redis.from_url(
                settings.REDIS_URL, socket_connect_timeout=3, socket_timeout=3
            ).ping()
        )
    except Exception:
        checks["redis"] = False
    try:
        storage_client().list_buckets()
        checks["minio"] = True
    except Exception:
        checks["minio"] = False
    try:
        search_client().get_collections()
        checks["qdrant"] = True
    except Exception:
        checks["qdrant"] = False
    probe = probe_ollama()
    checks["llm_model"] = availability(default_config(), probe)["available"]
    checks["embedding_model"] = any(
        n == settings.EMBEDDING_MODEL or n == settings.EMBEDDING_MODEL + ":latest"
        for n in probe["names"]
    )
    return JSONResponse(
        {
            "ready": all(checks.values()),
            "checks": checks,
            "note": "DeepSeek key presence only, no paid API call. Does not verify Celery worker/beat or reranker weights; run sample pipeline.",
        },
        status_code=200 if all(checks.values()) else 503,
    )


from app.api.lifecycle import router as lifecycle_router

app.include_router(lifecycle_router, prefix=settings.API_V1_PREFIX)

from app.api.metrics import router as metrics_router

app.include_router(metrics_router, prefix=settings.API_V1_PREFIX)

from app.api.bases import router as bases_router

app.include_router(bases_router, prefix=settings.API_V1_PREFIX)

from app.api.inventory import router as inventory_router

app.include_router(inventory_router, prefix=settings.API_V1_PREFIX)

from app.api.quality import router as quality_router

app.include_router(quality_router, prefix=settings.API_V1_PREFIX)


from app.api.workspace import router as workspace_router

app.include_router(workspace_router, prefix=settings.API_V1_PREFIX)

from app.api.developer import router as developer_router
app.include_router(developer_router, prefix=settings.API_V1_PREFIX)

from app.api.regex_experiments import router as regex_experiments_router
app.include_router(regex_experiments_router, prefix=settings.API_V1_PREFIX)

from app.api.laboratory import router as laboratory_router
app.include_router(laboratory_router,prefix=settings.API_V1_PREFIX)
from app.api.prediction_exports import router as prediction_exports_router
app.include_router(prediction_exports_router,prefix=settings.API_V1_PREFIX)

from app.api.b1_experiments import router as b1_experiments_router
app.include_router(b1_experiments_router, prefix=settings.API_V1_PREFIX)
