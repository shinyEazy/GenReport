from fastapi import FastAPI

from app.api.v1 import api_router


app = FastAPI(
    title="GenReport Engine",
    description="Stateless internal report execution engine for AXIOM.",
    version="1.0.0",
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
        "endpoints": ["POST /api/v1/reports:stream"],
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
