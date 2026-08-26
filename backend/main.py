from contextlib import asynccontextmanager
import logging
import os

from fastapi import FastAPI

from app.api.v1 import api_router


_log_level_name = os.getenv("LOG_LEVEL", "INFO").upper()
_log_level = getattr(logging, _log_level_name, logging.INFO)
logging.getLogger().setLevel(_log_level)
logging.getLogger("app").setLevel(_log_level)


def _configure_application_logging() -> None:
    # Uvicorn applies its logging configuration after importing the app module.
    # Re-apply the configured level at startup so workflow diagnostics are emitted.
    logging.getLogger().setLevel(_log_level)
    logging.getLogger("app").setLevel(_log_level)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _configure_application_logging()
    yield


app = FastAPI(
    title="GenReport Engine",
    description="Stateless internal report execution engine for AXIOM.",
    version="1.0.0",
    lifespan=lifespan,
)
app.include_router(api_router, prefix="/api/v1")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "healthy", "service": "gen-report-engine"}


@app.get("/api/v1/capabilities")
async def capabilities() -> dict[str, object]:
    return {
        "schema_version": "1",
        "service": "gen-report-engine",
        "endpoints": [
            "POST /api/v1/reports:stream",
            "POST /api/v1/reports:extract-dashboard",
        ],
        "persistence": False,
        "execution_backend": "axiom-runtime-gateway",
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
