from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    port: int = 8000
    log_level: str = "INFO"

    # Base vectorial
    vector_db_host: str = "localhost"
    vector_db_port: int = 6333
    vector_db_url: str | None = None  # ":memory:" para pruebas
    qdrant_collection: str = "documentacion_tributaria"

    # Embeddings: "fastembed" (denso multilingüe + BM25 español + CLIP visión) o "hash" (pruebas sin red)
    embeddings_backend: str = "fastembed"
    text_embedding_model: str = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
    sparse_embedding_model: str = "Qdrant/bm25"
    sparse_language: str = "spanish"
    clip_vision_model: str = "Qdrant/clip-ViT-B-32-vision"
    embedding_threads: int | None = None  # None = todos los núcleos

    # Herramientas MCP (expedientes del despacho)
    fastmcp_tools_url: str = "http://localhost:9000"
    mcp_approval_secret: str = "change-me-approval-secret"

    # Identidad (Keycloak OIDC)
    auth_mode: str = "keycloak"  # "keycloak" | "disabled" (solo desarrollo local / tests)
    keycloak_server_url: str = "http://localhost:8080/"
    keycloak_realm: str = "asesoria-realm"
    keycloak_allowed_issuers: str = "http://localhost:8080/realms/asesoria-realm,http://localhost/realms/asesoria-realm,https://localhost/realms/asesoria-realm"
    keycloak_allowed_clients: str = "pwa-client"
    role_cliente: str = "cliente"
    role_gestor: str = "gestor"

    # Revisión humana: las respuestas a clientes pasan por un gestor antes de entregarse
    review_client_answers: bool = True

    # Protección de datos: enmascarar NIF/NIE/CIF, IBAN, teléfonos y correos antes de llamar al LLM
    pii_masking: bool = True

    # Modelos
    llm_provider_order: str = "anthropic,mistral,openai,ollama,mock"
    llm_temperature: float = 0.0
    llm_max_tokens: int = 4096
    anthropic_api_key: str | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_max_tokens: int = 16000
    anthropic_effort: str = "high"
    anthropic_progress_updates: bool = True  # notas de progreso entre herramientas (beta) para la traza
    mistral_api_key: str | None = None
    mistral_text_model: str = "mistral-large-latest"
    mistral_vision_model: str = "pixtral-12b-latest"
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o"
    ollama_base_url: str | None = None
    ollama_vision_model: str = "llama3.2-vision"
    allow_mock_llm: bool = True

    # Agentes y RAG
    react_max_iterations: int = 8
    rag_top_k: int = 8
    media_root: Path = Path("/app/static/extracted_images")
    a2a_remote_agents: str = ""
    public_base_url: str = "http://localhost:8000"

    @property
    def qdrant_location(self) -> str:
        return self.vector_db_url or f"http://{self.vector_db_host}:{self.vector_db_port}"

    @property
    def jwks_url(self) -> str:
        return f"{self.keycloak_server_url.rstrip('/')}/realms/{self.keycloak_realm}/protocol/openid-connect/certs"

    @property
    def mcp_endpoint(self) -> str:
        return f"{self.fastmcp_tools_url.rstrip('/')}/mcp"


@lru_cache
def get_settings() -> Settings:
    return Settings()
