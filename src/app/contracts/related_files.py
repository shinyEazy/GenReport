from pydantic import BaseModel, ConfigDict, Field


class DiscoverySeed(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    document_id: str = Field(min_length=1, max_length=512)
    object_key: str = Field(min_length=1, max_length=2048)
    bucket: str = Field(min_length=1, max_length=255)


class RelatedFilesRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    organization_id: str = Field(min_length=1)
    workspace_id: str = Field(min_length=1)
    files: list[DiscoverySeed] = Field(min_length=1, max_length=20)


class RelatedFile(BaseModel):
    document_id: str
    object_key: str
    bucket: str
    filename: str


class RelatedFilesResponse(BaseModel):
    files: list[RelatedFile]
