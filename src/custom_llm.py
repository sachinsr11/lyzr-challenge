from lyzr_automata.ai_models.model_base import AIModel
from litellm import completion
import logging

logger = logging.getLogger(__name__)

class CustomLiteLLM(AIModel):
    def __init__(self, api_key, parameters):
        """
        Create LiteLLM-backed model adapter used by Lyzr tasks.

        Input (sample):
        - api_key: "AIza..."
        - parameters: {"model": "gemini/gemini-2.5-flash-lite", "temperature": 0.2, "max_tokens": 2000}

        Output (sample):
        - Initialized CustomLiteLLM instance with stored credentials/config.
        """
        # FIX: Removed arguments from super().__init__()
        self.api_key = api_key
        self.parameters = parameters

    def generate_text(self, task_id=None, system_persona=None, prompt=None):
        """
        Generate chat completion text for a single task prompt.

        Input (sample):
        - task_id: "security-task-1"
        - system_persona: "You are a Security Auditor"
        - prompt: "Analyze this diff hunk..."

        Output (sample):
        - "[{\"file\": \"app.py\", \"line\": 12, ...}]"
        - "" (empty string on provider/runtime failure)
        """
        try:
            model_name = self.parameters.get("model", "gemini/gemini-1.5-flash")
            temperature = self.parameters.get("temperature", 0.2)
            max_tokens = self.parameters.get("max_tokens", 2000)

            # Construct messages for Chat Models (Gemini/GPT)
            messages = [
                {"role": "system", "content": system_persona},
                {"role": "user", "content": prompt},
            ]

            # LiteLLM handles the complexity of calling Google/OpenAI/Anthropic
            response = completion(
                model=model_name,
                messages=messages,
                api_key=self.api_key,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            
            return response["choices"][0]["message"]["content"]

        except Exception as e:
            logger.error(f"LiteLLM generation failed for model {self.parameters.get('model')}: {e}")
            return ""
    
    def generate_image(self, task_id=None, prompt=None):
        """
        Placeholder image generation method required by abstract base class.

        Input (sample):
        - task_id: "img-1"
        - prompt: "Generate architecture diagram"

        Output (sample):
        - Raises NotImplementedError("Image generation not supported.")
        """
        raise NotImplementedError("Image generation not supported.")