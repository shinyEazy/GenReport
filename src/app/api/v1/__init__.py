from fastapi import APIRouter

from app.api.v1 import related_files, reports


api_router = APIRouter()
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(related_files.router, prefix="/reports", tags=["reports"])


__all__ = ["api_router", "reports"]
