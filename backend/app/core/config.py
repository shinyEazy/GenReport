from __future__ import annotations

import os
from pathlib import Path

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


BACKEND_DIR = Path(__file__).resolve().parents[2]


def get_llm_config_warnings(base_url: str, api_key: str) -> list[str]:
    warnings: list[str] = []
    if "openrouter.ai" in (base_url or "").lower() and api_key:
        if not api_key.startswith("sk-or-v1-"):
            warnings.append(
                "OPENAI_BASE_URL points to OpenRouter, but OPENAI_API_KEY does "
                "not look like an OpenRouter key."
            )
    return warnings


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_DIR / ".env",
        extra="ignore",
    )

    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    DEFAULT_MODEL: str = Field(
        default="",
        validation_alias=AliasChoices("DEFAULT_MODEL", "MODEL"),
    )
    MODEL_LIST: list[str] = Field(
        default=["qwen/qwen3.7-flash"],
        validation_alias=AliasChoices("MODEL_LIST", "AVAILABLE_MODELS"),
    )
    MULTIMODAL_MODELS: list[str] = Field(
        default=["qwen/qwen3.7-flash"],
    )
    MULTIMODAL_IMAGE_DETAIL: str = "high"
    MULTIMODAL_IMAGE_MAX_BYTES: int = Field(
        default=8 * 1024 * 1024,
        ge=1,
    )

    LANGSMITH_TRACING: bool = False
    LANGCHAIN_TRACING_V2: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGCHAIN_API_KEY: str = ""
    LANGSMITH_PROJECT: str = ""
    LANGCHAIN_PROJECT: str = ""
    LANGSMITH_ENDPOINT: str = ""
    LANGCHAIN_ENDPOINT: str = ""

    MAX_AGENT_ITERATIONS: int = Field(default=100, ge=1, le=100)
    METHOD_HUB_MCP_URL: str = "http://host.docker.internal:38000/mcp"
    REPORT_DISCOVERY_MAX_ARTIFACTS: int = Field(default=20, ge=1, le=100)
    REPORT_DISCOVERY_MAX_ROUNDS: int = Field(default=20, ge=1, le=100)
    LOCAL_MODE: bool = False
    LOCAL_WORKSPACE_ROOT: Path = BACKEND_DIR / "data" / "workspaces"
    LOCAL_EXECUTION_TIMEOUT_SECONDS: int = Field(default=120, ge=1, le=3600)

    @model_validator(mode="after")
    def set_default_model_from_list(self):
        if not self.MODEL_LIST:
            raise ValueError("MODEL_LIST must contain at least one model")
        if not self.DEFAULT_MODEL:
            self.DEFAULT_MODEL = self.MODEL_LIST[0]
        return self

    @model_validator(mode="after")
    def normalize_langsmith_settings(self):
        tracing = self.LANGSMITH_TRACING or self.LANGCHAIN_TRACING_V2
        self.LANGSMITH_TRACING = tracing
        self.LANGCHAIN_TRACING_V2 = tracing

        api_key = self.LANGSMITH_API_KEY or self.LANGCHAIN_API_KEY
        self.LANGSMITH_API_KEY = api_key
        self.LANGCHAIN_API_KEY = api_key

        project = self.LANGSMITH_PROJECT or self.LANGCHAIN_PROJECT or "gen-report"
        self.LANGSMITH_PROJECT = project
        self.LANGCHAIN_PROJECT = project

        endpoint = (
            self.LANGSMITH_ENDPOINT
            or self.LANGCHAIN_ENDPOINT
            or "https://api.smith.langchain.com"
        )
        self.LANGSMITH_ENDPOINT = endpoint
        self.LANGCHAIN_ENDPOINT = endpoint
        return self

    @property
    def AVAILABLE_MODELS(self) -> list[str]:
        return self.MODEL_LIST

    @property
    def LLM_CONFIG_WARNINGS(self) -> list[str]:
        return get_llm_config_warnings(self.OPENAI_BASE_URL, self.OPENAI_API_KEY)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return init_settings, env_settings, dotenv_settings, file_secret_settings

def configure_langsmith_environment(value: Settings) -> None:
    tracing = "true" if value.LANGSMITH_TRACING else "false"
    os.environ["LANGSMITH_TRACING"] = tracing
    os.environ["LANGCHAIN_TRACING_V2"] = tracing
    os.environ["LANGSMITH_PROJECT"] = value.LANGSMITH_PROJECT
    os.environ["LANGCHAIN_PROJECT"] = value.LANGSMITH_PROJECT
    os.environ["LANGSMITH_ENDPOINT"] = value.LANGSMITH_ENDPOINT
    os.environ["LANGCHAIN_ENDPOINT"] = value.LANGSMITH_ENDPOINT
    if value.LANGSMITH_API_KEY:
        os.environ["LANGSMITH_API_KEY"] = value.LANGSMITH_API_KEY
        os.environ["LANGCHAIN_API_KEY"] = value.LANGSMITH_API_KEY


settings = Settings()
configure_langsmith_environment(settings)
