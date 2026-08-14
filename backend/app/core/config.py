import os

from pydantic import AliasChoices, Field, model_validator
from pydantic_settings import BaseSettings
from typing import List
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]


def get_llm_config_warnings(base_url: str, api_key: str) -> List[str]:
    warnings = []
    normalized_base_url = (base_url or "").lower()
    normalized_api_key = api_key or ""

    if "openrouter.ai" in normalized_base_url and normalized_api_key:
        if not normalized_api_key.startswith("sk-or-v1-"):
            warnings.append(
                "OPENAI_BASE_URL points to OpenRouter, but OPENAI_API_KEY does not look like an OpenRouter key. "
                "Use an OpenRouter key starting with sk-or-v1-, or change OPENAI_BASE_URL to your key provider."
            )

    return warnings


class Settings(BaseSettings):
    # Local-first defaults. The open-source build stores data under ./data.
    DATABASE_URL: str = "sqlite:///./data/lambda_local.db"
    LOCAL_MODE: bool = True
    LOCAL_USER_EMAIL: str = "local@lambda.local"
    LOCAL_USER_NAME: str = "Local User"
    
    # Legacy auth utility defaults. Local mode does not require these in .env.
    SECRET_KEY: str = "your-super-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10080  # 7 days
    
    # LLM Configuration
    OPENAI_API_KEY: str = ""
    OPENAI_BASE_URL: str = "https://api.openai.com/v1"
    DEFAULT_MODEL: str = Field(
        default="",
        validation_alias=AliasChoices("DEFAULT_MODEL", "MODEL"),
    )

    # Models exposed in the UI. The first model is used as the default when
    # DEFAULT_MODEL is not set, and the IDs are sent unchanged to OPENAI_BASE_URL.
    MODEL_LIST: List[str] = Field(
        default=[
            "mimo-v2.5-pro",
            "deepseek-v4-pro",
        ],
        validation_alias=AliasChoices("MODEL_LIST", "AVAILABLE_MODELS"),
    )
    MULTIMODAL_MODELS: List[str] = [
        "mimo-v2.5-pro",
        "mimo-v2.5",
    ]
    MULTIMODAL_IMAGE_DETAIL: str = "high"
    MULTIMODAL_IMAGE_MAX_BYTES: int = 8 * 1024 * 1024

    # Accept current LangSmith names and legacy LangChain names together.
    LANGSMITH_TRACING: bool = False
    LANGCHAIN_TRACING_V2: bool = False
    LANGSMITH_API_KEY: str = ""
    LANGCHAIN_API_KEY: str = ""
    LANGSMITH_PROJECT: str = ""
    LANGCHAIN_PROJECT: str = ""
    LANGSMITH_ENDPOINT: str = ""
    LANGCHAIN_ENDPOINT: str = ""

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
    def AVAILABLE_MODELS(self) -> List[str]:
        return self.MODEL_LIST

    @property
    def LLM_CONFIG_WARNINGS(self) -> List[str]:
        return get_llm_config_warnings(self.OPENAI_BASE_URL, self.OPENAI_API_KEY)
    
    # Code Execution
    CODE_EXECUTION_TIMEOUT: int = 900
    MAX_OUTPUT_LENGTH: int = 1000000
    
    # Local open-source build executes tools in a local workspace directory.
    CODE_EXECUTION_MODE: str = "local"
    LOCAL_WORKSPACE_ROOT: str = "./data/workspaces"
    
    # OpenSandbox Configuration
    # Custom sandbox image with additional packages (e.g., LaTeX)
    # Build custom image: cd sandbox-docker && docker build -t lambda-sandbox:latest .
    SANDBOX_IMAGE: str = "opensandbox/code-interpreter:v1.0.2"
    
    # OpenSandbox container max lifetime. OpenSandbox treats this as container
    # TTL, not app-level idle timeout, so keep it comfortably above user work.
    SANDBOX_CONTAINER_TIMEOUT_MINUTES: int = 180

    # App-level idle timeout. The backend reaps sessions after this much
    # inactivity, while active sandboxes can live longer than 30 minutes.
    SANDBOX_IDLE_TIMEOUT_MINUTES: int = 30
    SANDBOX_CLEANUP_ORPHANS_ON_STARTUP: bool = True
    SANDBOX_PREINSTALLED_PACKAGES: bool = False
    
    # CORS
    FRONTEND_URL: str = "http://localhost:3000"
    
    # Agent Configuration
    MAX_AGENT_ITERATIONS: int = 50  # Maximum tool execution iterations per conversation
    METHOD_HUB_MCP_URL: str = "http://host.docker.internal:38000/mcp"
    REPORT_DISCOVERY_MAX_ARTIFACTS: int = 20
    REPORT_DISCOVERY_MAX_ROUNDS: int = 20
    
    # Aliyun OSS Configuration (unused in local mode)
    ALIYUN_OSS_ACCESS_KEY_ID: str = ""
    ALIYUN_OSS_ACCESS_KEY_SECRET: str = ""
    ALIYUN_OSS_BUCKET_NAME: str = "lambda-app-prod"
    ALIYUN_OSS_ENDPOINT: str = "oss-cn-hongkong.aliyuncs.com"
    ALIYUN_OSS_BASE_URL: str = "https://lambda-app-prod.oss-cn-hongkong.aliyuncs.com"
    
    # File Storage Mode
    FILE_STORAGE_MODE: str = "local"

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        return init_settings, dotenv_settings, env_settings, file_secret_settings
    
    class Config:
        env_file = BACKEND_DIR / ".env"


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
