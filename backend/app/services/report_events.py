from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from app.contracts.report_execution import (
    ReportCompletion,
    ReportEvent,
    ReportEventType,
    ReportExecutionRequest,
    ReportFailure,
    ReportUsage,
)


class ReportEventFactory:
    def __init__(self, request: ReportExecutionRequest) -> None:
        self.request = request

    def create(
        self,
        event_type: ReportEventType,
        payload: dict[str, Any],
    ) -> ReportEvent:
        validated_payload = self._validated_payload(event_type, payload)
        return ReportEvent(
            event_id=f"evt_{uuid.uuid4().hex}",
            type=event_type,
            occurred_at=datetime.now(timezone.utc),
            operation_id=self.request.operation_id,
            response_id=self.request.response_id,
            run_id=self.request.run_id,
            organization_id=self.request.organization_id,
            workspace_id=self.request.workspace_id,
            trace_id=self.request.trace_id,
            payload=validated_payload,
        )

    @staticmethod
    def _validated_payload(
        event_type: ReportEventType,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        models = {
            "report.usage": ReportUsage,
            "report.failed": ReportFailure,
            "report.completed": ReportCompletion,
        }
        model = models.get(event_type)
        if model is None:
            return dict(payload)
        return model.model_validate(payload).model_dump(mode="json", exclude_none=True)


def encode_report_sse(event: ReportEvent) -> str:
    return (
        f"id: {event.event_id}\n"
        f"event: {event.type}\n"
        f"data: {event.model_dump_json()}\n\n"
    )
