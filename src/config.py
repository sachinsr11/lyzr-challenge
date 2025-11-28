from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "Lyzr PR Agent"
    LOG_LEVEL: str = "INFO"
    
    # Secrets
    GOOGLE_API_KEY: str = "" 
    GITHUB_TOKEN: str = ""
    WEBHOOK_SECRET: str = ""

    ENABLE_SECURITY_SCAN: bool = True
    ENABLE_QUALITY_SCAN: bool = True
    # Model Configuration (Gemini via LiteLLM)
    SECURITY_MODEL_NAME: str = "gemini/gemini-2.0-flash-live"
    QUALITY_MODEL_NAME: str = "gemini/gemini-2.0-flash-live"
    ARCHITECT_MODEL_NAME: str = "gemini/gemini-2.0-flash-live"
    SYNTHESIZER_MODEL_NAME: str = "gemini/gemini-2.0-flash-live"

    class Config:
        env_file = ".env"

settings = Settings()