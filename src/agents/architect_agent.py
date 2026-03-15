import logging
import json
from lyzr_automata import Agent, Task
from lyzr_automata.pipelines.linear_sync_pipeline import LinearSyncPipeline
from src.custom_llm import CustomLiteLLM
from src.models import ReviewComment
from src.config import settings
from src.prompts import ARCHITECT_PERSONA, ARCHITECT_INSTRUCTION_SUFFIX
from src.utils import clean_json_output, validate_json_structure

logger = logging.getLogger(__name__)


class ArchitectAgent:
    def __init__(self):
        """
        Initialize architecture-focused analysis model and architect persona agent.

        Input (sample):
        - None (reads settings.GOOGLE_API_KEY and ARCHITECT_MODEL_NAME)

        Output (sample):
        - ArchitectAgent instance with configured llm_model and Agent(role="Software Architect").
        """
        self.llm_model = CustomLiteLLM(
            api_key=settings.GOOGLE_API_KEY,
            parameters={
                "model": settings.ARCHITECT_MODEL_NAME,
                "temperature": 0.2,
                "max_tokens": 2000,
            },
        )

        self.agent = Agent(
            role="Software Architect",
            prompt_persona=ARCHITECT_PERSONA
        )

    def analyze(self, content: str, filename: str, start_line: int) -> list[ReviewComment]:
        """
        Analyze one diff hunk for maintainability and architectural-pattern issues.

        Input (sample):
        - content: "+ global_state['db'] = connect()"
        - filename: "src/main.py"
        - start_line: 30

        Output (sample):
        - [ReviewComment(file="src/main.py", line=31, type="Architect", severity="Medium", message="Global mutable state introduced", suggestion="Inject dependency via constructor")]
        - [] when no architectural concerns or parse/runtime failure
        """
        logger.info(f"Architect analyzing: {filename}")

        # 1. Prepare instruction
        instruction = f"""
        Analyze the following DIFF HUNK from '{filename}'.

        {ARCHITECT_INSTRUCTION_SUFFIX}
        
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
            "type": "Architect",
            "severity": "Critical"|"High"|"Medium"|"Low",
            "message": "<Issue Description>",
            "suggestion": "<Refactoring Advice>"
          }}
        ]
        
        Return ONLY valid JSON. If no architectural issues are found, return [].
        Do not include markdown formatting like ```json ... ```.
        """
        
        # 2. Create Task
        task = Task(
            name="Architectural Review",
            model=self.llm_model,
            agent=self.agent,
            instructions=instruction,
        )

        try:
            # 3. Run Pipeline
            response = LinearSyncPipeline(
                name="Architectural Analysis Pipeline",
                completion_message="Analysis complete",
                tasks=[task],
            ).run()
            
            # 4. Parse output
            raw_output = response[0]['task_output'] if isinstance(response, list) else response
            cleaned_output = clean_json_output(raw_output)
            
            if not validate_json_structure(cleaned_output):
                logger.error(f"Invalid JSON structure from Architect Agent for {filename}")
                return []
            
            json_data = json.loads(cleaned_output)
            return [ReviewComment(**item) for item in json_data]

        except json.JSONDecodeError:
            logger.error(f"Failed to parse JSON from Architect Agent for {filename}. Raw: {raw_output}")
            return []
        except Exception as e:
            logger.error(f"Architect Agent failed on {filename}: {e}")
            return []

