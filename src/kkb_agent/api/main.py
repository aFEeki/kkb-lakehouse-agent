"""Local-only readiness checks; external services are not startup dependencies."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from kkb_agent.catalog.duckdb_store import DuckDBStore
from kkb_agent.catalog.lance_store import LanceStore
from kkb_agent.config import Settings

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        config = settings if settings is not None else Settings()
        app.state.stores = {
            "duckdb": DuckDBStore(config.duckdb_path),
            "lancedb": LanceStore(config.lancedb_path),
        }
        yield

    application = FastAPI(title="KKB Lakehouse Agent", lifespan=lifespan)
    config = settings if settings is not None else Settings()
    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors_origins,
        allow_methods=["GET"],
        allow_headers=["Accept"],
    )

    @application.get("/health")
    def health():
        components = {"application": "ok"}
        for name, store in application.state.stores.items():
            try:
                components[name] = "ok" if store.check() else "error"
            except Exception:
                logger.exception("Local readiness check failed: %s", name)
                components[name] = "error"
        ready = all(value == "ok" for value in components.values())
        return JSONResponse(
            {"status": "ok" if ready else "degraded", "components": components},
            status_code=200 if ready else 503,
        )

    return application


app = create_app()
