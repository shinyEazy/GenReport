from pydantic import AnyHttpUrl, BaseModel, EmailStr, Field, model_validator
from typing import Optional, List, Dict, Any, Literal
from datetime import datetime
from uuid import UUID


# User schemas
class UserBase(BaseModel):
    email: EmailStr
    full_name: Optional[str] = None


class UserResponse(UserBase):
    id: int
    display_name: Optional[str] = None
    is_active: bool
    is_admin: bool
    plan: Optional[str] = None
    invite_code: Optional[str] = None
    last_login_at: Optional[datetime] = None
    created_at: datetime
    
    class Config:
        from_attributes = True


# Message schemas
class MessageBase(BaseModel):
    role: str
    content: str
    tool_calls: Optional[str] = None
    tool_call_id: Optional[str] = None


class MessageCreate(MessageBase):
    conversation_id: Optional[int] = None


class MessageResponse(MessageBase):
    id: int
    conversation_id: int
    created_at: datetime
    
    class Config:
        from_attributes = True


# Conversation schemas
class ConversationBase(BaseModel):
    title: Optional[str] = None
    model: Optional[str] = None


class ConversationCreate(ConversationBase):
    pass


class ConversationUpdate(BaseModel):
    title: Optional[str] = None


class ConversationResponse(ConversationBase):
    id: int
    hash_id: str
    user_id: int
    created_at: datetime
    updated_at: datetime
    message_count: Optional[int] = 0
    
    class Config:
        from_attributes = True


class ConversationDetailResponse(ConversationResponse):
    messages: List[MessageResponse] = []
    
    class Config:
        from_attributes = True


# Chat schemas
class ExecutionFileRequest(BaseModel):
    artifact_id: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    sandbox_path: str = Field(pattern=r"^/workspace/runs/[^/]+/inputs/")
    content_type: str = "application/octet-stream"
    size: int = Field(ge=0)
    checksum: Optional[str] = None


class ExecutionContextRequest(BaseModel):
    version: Literal["v1"] = "v1"
    run_id: str
    conversation_id: str
    sandbox_id: UUID
    execution_workspace_id: UUID
    gateway_url: AnyHttpUrl
    capability_token: str = Field(min_length=1)
    expires_at: int
    input_path: str
    work_path: str
    output_path: str
    capabilities: List[str]

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


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None  # hash_id
    model: Optional[str] = None
    files: Optional[List[int]] = None  # List of uploaded file IDs
    analysis_mode: Optional[str] = None
    language: Optional[str] = None
    runtime_gateway: Optional[Dict[str, Any]] = None
    execution_context: Optional[ExecutionContextRequest] = None
    execution_files: List[ExecutionFileRequest] = Field(default_factory=list)


class ChatStreamResponse(BaseModel):
    type: str  # "delta", "tool_call", "done", "error"
    content: Optional[str] = None
    tool_name: Optional[str] = None
    tool_input: Optional[Dict[str, Any]] = None
    tool_output: Optional[str] = None


# File schemas
class FileUploadResponse(BaseModel):
    id: int
    filename: str
    original_name: str
    file_size: int
    mime_type: str
    created_at: datetime
    
    class Config:
        from_attributes = True


# Code execution schemas
class CodeExecutionRequest(BaseModel):
    code: str
    language: str = "python"
    timeout: Optional[int] = 60


class CodeExecutionResponse(BaseModel):
    success: bool
    stdout: str
    stderr: str
    exit_code: int
    execution_time: float
