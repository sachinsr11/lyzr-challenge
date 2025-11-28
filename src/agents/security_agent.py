import logging
import json
from lyzr_automata import Agent, Task
from lyzr_automata.pipelines.linear_sync_pipeline import LinearSyncPipeline
from src.custom_llm import CustomLiteLLM
from src.models import ReviewComment
from src.config import settings
from src.prompts import SECURITY_PERSONA, SECURITY_INSTRUCTION_SUFFIX
from src.utils import clean_json_output, validate_json_structure

logger = logging.getLogger(__name__)


class SecurityAgent:
    def __init__(self):
        # Use CustomLiteLLM for Gemini API routing
        self.llm_model = CustomLiteLLM(
            api_key=settings.GOOGLE_API_KEY,
            parameters={
                "model": settings.SECURITY_MODEL_NAME,
                "temperature": 0.1,  # Very low temp for strict analysis
                "max_tokens": 2000,
            },
        )

        self.agent = Agent(
            role="Security Auditor",
            prompt_persona=SECURITY_PERSONA
        )

    def analyze(self, content: str, filename: str, start_line: int) -> list[ReviewComment]:
        """
        Scans the code chunk for vulnerabilities using Lyzr.
        """
        logger.info(f"Security scan on: {filename}")

        # 1. Prepare Instruction with strict JSON schema enforcement
        instruction = f"""
        Analyze the following DIFF HUNK from '{filename}'.

        {SECURITY_INSTRUCTION_SUFFIX}

        IMPORTANT:
        - This hunk begins at line {start_line} in the actual file.
        - If you detect an issue on a line inside this hunk, compute the REAL file line number as:
            real_line = {start_line} + (line_number_inside_hunk)

        - For example:
            If issue is in "+5" inside hunk → real_line = {start_line} + 5

        Return STRICT JSON ONLY.

        CODE HUNK:
        {content}
        
        Return a JSON list of objects with this schema:
        [
          {{
            "file": "{filename}",
            "line": <real_line>,
            "type": "Security",
            "severity": "Critical"|"High"|"Medium"|"Low",
            "message": "<Issue Description>",
            "suggestion": "<Refactoring Advice>"
          }}
        ]
        
        CRITICAL: If the code is safe or contains no obvious vulnerabilities, return strictly [].
        Do not hallucinate issues. Do not include markdown formatting like ```json ... ```.
        """

        # 2. Create Task
        task = Task(
            name="Security Audit",
            model=self.llm_model,
            agent=self.agent,
            instructions=instruction,
        )

        try:
            # 3. Run Pipeline
            response = LinearSyncPipeline(
                name="Security Analysis Pipeline",
                completion_message="Security scan complete",
                tasks=[task],
            ).run()
            
            # 4. Parse Output & Return Models
            # Extract text from response (Lyzr returns a dict or object depending on version)
            raw_output = response[0]['task_output'] if isinstance(response, list) else response
            
            # Clean markdown code blocks if present
            cleaned_output = clean_json_output(raw_output)
            
            # Validate before parsing
            if not validate_json_structure(cleaned_output):
                logger.error(f"Invalid JSON structure from Security Agent for {filename}")
                return []
            
            json_data = json.loads(cleaned_output)
            
            return [ReviewComment(**item) for item in json_data]

        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON from Security Agent for {filename}. Raw: {raw_output}")
            return []
        except Exception as e:
            logger.error(f"Security Agent failed on {filename}: {e}")
            return []
