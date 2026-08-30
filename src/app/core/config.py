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
    REPORT_DASHBOARD_MAX_PAGE_IMAGES: int = Field(default=8, ge=0, le=16)

    LANGCHAIN_TRACING_V2: bool = False
    LANGCHAIN_API_KEY: str = ""
    LANGCHAIN_PROJECT: str = ""
    LANGCHAIN_ENDPOINT: str = ""

    MAX_AGENT_ITERATIONS: int = Field(default=100, ge=1, le=100)
    METHOD_HUB_MCP_URL: str = "http://host.docker.internal:38000/mcp"
    REPORT_DISCOVERY_MAX_ARTIFACTS: int = Field(default=20, ge=1, le=100)
    REPORT_DISCOVERY_MAX_ROUNDS: int = Field(default=25, ge=1, le=100)
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
    def normalize_langchain_settings(self):
        self.LANGCHAIN_PROJECT = self.LANGCHAIN_PROJECT or "gen-report"
        self.LANGCHAIN_ENDPOINT = (
            self.LANGCHAIN_ENDPOINT or "https://api.smith.langchain.com"
        )
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
    tracing = "true" if value.LANGCHAIN_TRACING_V2 else "false"
    os.environ["LANGCHAIN_TRACING_V2"] = tracing
    os.environ["LANGCHAIN_PROJECT"] = value.LANGCHAIN_PROJECT
    os.environ["LANGCHAIN_ENDPOINT"] = value.LANGCHAIN_ENDPOINT
    if value.LANGCHAIN_API_KEY:
        os.environ["LANGCHAIN_API_KEY"] = value.LANGCHAIN_API_KEY


settings = Settings()
configure_langsmith_environment(settings)
