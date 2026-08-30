from fastapi import APIRouter

from app.api.v1 import reports


api_router = APIRouter()
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])


__all__ = ["api_router", "reports"]
