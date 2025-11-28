from lyzr_automata.ai_models.model_base import AIModel
from litellm import completion
import logging

logger = logging.getLogger(__name__)

class CustomLiteLLM(AIModel):
    def __init__(self, api_key, parameters):
        """
        Official Extension Pattern:
        We inherit from AIModel but handle initialization manually 
        because the base class does not accept arguments in this version.
        """
        # FIX: Removed arguments from super().__init__()
        self.api_key = api_key
        self.parameters = parameters

    def generate_text(self, task_id=None, system_persona=None, prompt=None):
        """
        Required method for Lyzr Automata to get text from an LLM.
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
        """Required abstract method, but we don't use it."""
        raise NotImplementedError("Image generation not supported.")