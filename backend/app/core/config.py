from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    app_env: str = "development"
    app_name: str = "강냉이 에스크"
    database_url: str = "sqlite:///./knuask.db"
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    openai_api_key: str | None = None
    openai_chat_model: str = "gpt-4.1-mini"
    gemini_api_key: str | None = None
    gemini_chat_model: str = "gemini-3.1-flash-lite"
    gemini_api_base_url: str = "https://generativelanguage.googleapis.com/v1beta"
    gemini_chat_timeout: float = 10.0
    gemini_chat_max_output_tokens: int = 1536
    gemini_thinking_level: str = "low"
    gemini_max_retries: int = 2
    # 개발 환경은 Codex 로그인 워커를, 학교 운영 환경은 동일 스키마의
    # Responses API 워커를 사용한다.
    openai_enrichment_model: str = "gpt-5.6-sol"
    openai_enrichment_reasoning_effort: str = "medium"
    openai_image_detail: str = "original"
    chat_provider: str = "auto"
    ollama_chat_model: str = "qwen3.5:4b"
    ollama_chat_keep_alive: str = "20m"
    ollama_chat_timeout: float = 240.0
    ollama_chat_num_ctx: int = 8192
    ollama_structuring_num_ctx: int = 16384
    ollama_chat_num_predict: int = 768
    ollama_structuring_num_predict: int = 2048
    local_ai_complex_queries_only: bool = True
    llm_candidate_reranking_enabled: bool = False
    notice_structuring_provider: str = "rules"
    codex_enrichment_enabled: bool = False
    on_demand_search_enabled: bool = True
    on_demand_live_search_enabled: bool = True
    on_demand_codex_enabled: bool = True
    on_demand_codex_provider: str = "codex_exec"
    on_demand_codex_model: str = "gpt-5.6-sol"
    on_demand_timeout_seconds: float = 30.0
    on_demand_page_timeout_seconds: float = 5.0
    on_demand_max_searches: int = 2
    on_demand_max_urls: int = 6
    on_demand_top_score_threshold: float = 0.58
    missing_evidence_recovery_enabled: bool = True
    missing_evidence_recovery_timeout_seconds: float = 12.0
    missing_evidence_negative_ttl_seconds: int = 3600
    missing_evidence_max_pdf_pages: int = 20
    on_demand_allowed_domains: list[str] = Field(default_factory=lambda: [
        "kangnam.ac.kr", "web.kangnam.ac.kr",
    ])
    on_demand_prompt_version: str = "ondemand-v22"
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 1536
    embedding_provider: str = "ollama"
    ollama_base_url: str = "http://host.docker.internal:11434"
    ollama_embedding_model: str = "bge-m3"
    ollama_embedding_keep_alive: str = "30m"
    embedding_request_timeout: float = 45.0
    mock_ai: bool = True
    mock_crawler: bool = True
    notice_base_url: str = "https://web.kangnam.ac.kr"
    notice_list_url: str = "https://web.kangnam.ac.kr/menu/f19069e6134f8f8aa7f689a4a675e66f.do"
    notice_detail_url: str = "https://web.kangnam.ac.kr/menu/board/info/f19069e6134f8f8aa7f689a4a675e66f.do"
    crawler_months: int = 12
    crawler_max_pages: int = 200
    crawler_incremental_pages: int = 10
    crawler_delay_seconds: float = 0.8
    crawler_detail_workers: int = 3
    crawler_include_events: bool = True
    crawler_event_detail_limit: int = 300
    crawler_extract_attachments: bool = True
    crawler_attachment_max_mb: int = 25
    crawler_attachment_cache_ttl_hours: int = 24
    crawler_attachment_cache_dir: str = "/tmp/knuask-attachment-cache"
    crawler_schedule_enabled: bool = True
    crawler_schedule_minutes: int = 60
    crawler_daily_schedule_hour: int = 2
    crawler_full_schedule_hour: int = 3
    crawler_full_schedule_day: str = "sun"
    max_input_length: int = 1000
    # 관리자 API는 명시적으로 비밀값을 설정하기 전까지 비활성화한다.
    admin_api_token: str | None = None
    schema_version: str = "2.0"
    embedding_version: str = "2.0"

    @field_validator("cors_origins", mode="before")
    @classmethod
    def split_origins(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("on_demand_allowed_domains", mode="before")
    @classmethod
    def split_allowed_domains(cls, value):
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
