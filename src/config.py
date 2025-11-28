from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "Lyzr PR Agent"
    LOG_LEVEL: str = "INFO"
    
    # Secrets
    GOOGLE_API_KEY: str = "" 
    GITHUB_TOKEN: str = ""
    WEBHOOK_SECRET: str = ""

    # Model Configuration (Gemini via LiteLLM)
    SECURITY_MODEL_NAME: str = "gemini/gemini-2.5-flash"
    QUALITY_MODEL_NAME: str = "gemini/gemini-2.5-flash"
    ARCHITECT_MODEL_NAME: str = "gemini/gemini-2.5-flash"
    SYNTHESIZER_MODEL_NAME: str = "gemini/gemini-2.5-flash"

    class Config:
        env_file = ".env"

settings = Settings()