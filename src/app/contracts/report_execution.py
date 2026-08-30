from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, model_validator


ReportEventType = Literal[
    "report.status",
    "report.inputs.selected",
    "report.tool.started",
    "report.tool.completed",
    "report.tool.failed",
    "report.output_text.delta",
    "report.usage",
    "report.failed",
    "report.completed",
]


class ReportHistoryMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=200_000)
    artifact_refs: list[str] = Field(default_factory=list, max_length=100)


class ExecutionFileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    sandbox_path: str = Field(pattern=r"^/workspace/runs/[^/]+/inputs/")
    content_type: str = "application/octet-stream"
    size: int = Field(ge=0)
    checksum: str | None = None
    source_id: str | None = Field(default=None, max_length=2048)
    document_id: str | None = Field(default=None, max_length=255)
    source_object_key: str | None = Field(default=None, max_length=2048)
    source_last_modified: datetime | None = None


class ExecutionContextRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["v1"] = "v1"
    run_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    sandbox_id: UUID
    execution_workspace_id: UUID
    gateway_url: AnyHttpUrl
    capability_token: str = Field(min_length=1)
    expires_at: int
    input_path: str
    work_path: str
    output_path: str
    capabilities: list[str]

    @model_validator(mode="after")
    def validate_run_scoped_paths(self):
        run_root = f"/workspace/runs/{self.run_id}"
        expected_paths = {
            "input_path": f"{run_root}/inputs",
            "work_path": f"{run_root}/work",
            "output_path": f"{run_root}/outputs",
        }
        for field_name, expected_path in expected_paths.items():
            if getattr(self, field_name) != expected_path:
                raise ValueError(
                    f"{field_name} must be exactly scoped to run_id {self.run_id!r}"
                )
        return self


class RuntimeGatewayCapability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=128)
    endpoint: AnyHttpUrl
    token: str = Field(min_length=1)
    token_type: Literal["bearer"] = "bearer"
    expires_at: int
    workspace_id: str | None = None
    capabilities: list[str] = Field(default_factory=list)


class SelectedFileReferenceRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    resource_id: str = Field(min_length=1, max_length=255)
    filename: str = Field(min_length=1, max_length=512)
    object_key: str = Field(min_length=1, max_length=2048)
    bucket: str = Field(min_length=1, max_length=255)
    content_type: str | None = Field(default=None, max_length=255)


class SelectedFilesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["all", "selected"] = "all"
    resource_ids: list[str] = Field(default_factory=list, max_length=100)
    resource_names: list[str] = Field(default_factory=list, max_length=100)
    resource_refs: list[SelectedFileReferenceRequest] = Field(
        default_factory=list,
        max_length=100,
    )

    @model_validator(mode="after")
    def validate_selected_resources(self):
        if any(not resource_id.strip() for resource_id in self.resource_ids):
            raise ValueError("selected_files.resource_ids must not contain blank values")
        if self.mode == "selected" and not self.resource_ids:
            raise ValueError(
                "selected_files.selected requires at least one resource_id"
            )
        if len(set(self.resource_ids)) != len(self.resource_ids):
            raise ValueError("selected_files.resource_ids must be unique")
        reference_ids = [reference.resource_id for reference in self.resource_refs]
        if len(set(reference_ids)) != len(reference_ids):
            raise ValueError("selected_files.resource_refs must be unique")
        if any(resource_id not in self.resource_ids for resource_id in reference_ids):
            raise ValueError(
                "selected_files.resource_refs must belong to resource_ids"
            )
        return self


class ReportExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    operation_id: str = Field(min_length=1, max_length=128)
    response_id: str = Field(min_length=1, max_length=128)
    run_id: str = Field(min_length=1, max_length=128)
    trace_id: str | None = Field(default=None, max_length=128)
    instruction: str = Field(min_length=1, max_length=500_000)
    history: list[ReportHistoryMessage] = Field(default_factory=list, max_length=200)
    model: str | None = Field(default=None, min_length=1, max_length=200)
    language: str = Field(default="auto", min_length=1, max_length=32)
    organization_id: str = Field(min_length=1, max_length=255)
    workspace_id: str = Field(min_length=1, max_length=255)
    execution_context: ExecutionContextRequest
    execution_files: list[ExecutionFileRequest] = Field(
        default_factory=list, max_length=100
    )
    runtime_gateway: RuntimeGatewayCapability
    discover_workspace_files: bool = False
    selected_files: SelectedFilesRequest | None = None
    workspace_discovery_instruction: str | None = Field(
        default=None,
        max_length=20_000,
    )
    primary_source_id: str | None = Field(default=None, max_length=2048)

    @model_validator(mode="after")
    def validate_run_scope(self):
        if self.execution_context.run_id != self.run_id:
            raise ValueError("execution_context.run_id must match run_id")
        if self.runtime_gateway.run_id != self.run_id:
            raise ValueError("runtime_gateway.run_id must match run_id")
        if self.runtime_gateway.workspace_id not in {None, self.workspace_id}:
            raise ValueError("runtime_gateway.workspace_id must match workspace_id")
        input_prefix = f"/workspace/runs/{self.run_id}/inputs/"
        for item in self.execution_files:
            if not item.sandbox_path.startswith(input_prefix):
                raise ValueError("execution file sandbox_path must match run_id")
        if self.primary_source_id is not None:
            primary_count = sum(
                item.source_id == self.primary_source_id
                for item in self.execution_files
            )
            if primary_count != 1:
                raise ValueError(
                    "primary_source_id must match exactly one execution file source_id"
                )
        return self


class ReportUsage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    estimated: bool = False


class ReportFailure(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    phase: Literal[
        "validation",
        "discovery",
        "model",
        "tool",
        "artifact",
        "authorization",
        "cancellation",
        "internal",
    ]
    message: str
    retryable: bool


class ReportArtifact(BaseModel):
    model_config = ConfigDict(extra="allow")

    artifact_ref: str
    filename: str
    content_type: str | None = None
    size: int | None = Field(default=None, ge=0)


class ReportCompletion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    output_text: str
    artifacts: list[ReportArtifact] = Field(default_factory=list)
    usage: ReportUsage | None = None


class SelectedReportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=2048)
    document_id: str | None = Field(default=None, max_length=255)
    object_key: str = Field(min_length=1, max_length=2048)
    filename: str = Field(min_length=1, max_length=512)
    content_type: str | None = Field(default=None, max_length=255)
    role: Literal["primary", "related"]


class ReportInputsSelected(BaseModel):
    model_config = ConfigDict(extra="forbid")

    inputs: list[SelectedReportInput] = Field(default_factory=list, max_length=100)


class ReportEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"] = "1"
    event_id: str
    type: ReportEventType
    producer: Literal["gen-report"] = "gen-report"
    occurred_at: datetime
    operation_id: str
    response_id: str
    run_id: str
    organization_id: str
    workspace_id: str
    trace_id: str | None = None
    payload: dict[str, Any]
