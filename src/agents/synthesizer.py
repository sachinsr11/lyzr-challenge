import logging
from typing import List
from src.models import ReviewComment
from src.config import settings
from lyzr_automata import Agent, Task
from lyzr_automata.pipelines.linear_sync_pipeline import LinearSyncPipeline
from src.custom_llm import CustomLiteLLM

logger = logging.getLogger(__name__)


class SynthesizerAgent:
    def __init__(self):
        # Use CustomLiteLLM for Gemini API routing
        self.llm_model = CustomLiteLLM(
            api_key=settings.GOOGLE_API_KEY,
            parameters={
                "model": settings.SYNTHESIZER_MODEL_NAME,
                "temperature": 0.5,
                "max_tokens": 500,
            },
        )
        
        self.agent = Agent(
            role="Technical Writer",
            prompt_persona="You are a Technical Writer summarizing code review findings for a developer audience."
        )

    def create_report(self, comments: List[ReviewComment]) -> str:
        """
        Takes a raw list of comments from all agents and generates a Markdown report.
        """
        if not comments:
            return "## ✅ Lyzr Review: No Issues Found\n\nYour code looks clean!"

        # 1. Deduplication Logic
        unique_comments = self._deduplicate(comments)

        # 2. Sort by Severity (Critical -> High -> Medium -> Low)
        # We assign a weight to each severity to sort them correctly
        severity_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        unique_comments.sort(key=lambda x: severity_order.get(x.severity, 4))

        # 3. Generate Summary (AI-powered)
        summary_header = self._generate_summary_header(unique_comments)

        # 4. Format Output
        # Part A: The Summary Table (For quick scanning)
        report_body = "### 📊 Findings Summary\n\n"
        report_body += "| Severity | Type | File | Line | Issue |\n"
        report_body += "| :--- | :--- | :--- | :--- | :--- |\n"

        for c in unique_comments:
            icon = self._get_icon(c.type)
            sev_icon = self._get_severity_icon(c.severity)
            # Clean message for table (remove newlines so table doesn't break)
            short_msg = c.message.split('\n')[0].replace("|", "-")
            report_body += f"| {sev_icon} **{c.severity}** | {icon} {c.type} | `{c.file}` | {c.line} | {short_msg} |\n"

        report_body += "\n"

        # Part B: Detailed Breakdown (Collapsible for cleaner UI)
        report_body += "<details>\n<summary><b>🔍 View Detailed Analysis & Code Fixes</b></summary>\n\n"
        
        # Group by file for cleaner reading in the details section
        comments_by_file = {}
        for c in unique_comments:
            if c.file not in comments_by_file:
                comments_by_file[c.file] = []
            comments_by_file[c.file].append(c)
            
        for filename, file_comments in comments_by_file.items():
            report_body += f"#### 📄 `{filename}`\n"
            for c in file_comments:
                icon = self._get_icon(c.type)
                sev_icon = self._get_severity_icon(c.severity)
                report_body += f"---\n"
                report_body += f"**{sev_icon} Line {c.line}** [{c.type}]\n"
                report_body += f"> {c.message}\n"
                if c.suggestion:
                    report_body += f"\n**Suggested Fix:**\n```python\n{c.suggestion}\n```\n"
            report_body += "\n"

        report_body += "</details>"

        return summary_header + "\n\n" + report_body

    def _deduplicate(self, comments: List[ReviewComment]) -> List[ReviewComment]:
        """
        Deterministic deduplication based on file+line+type+message.
        Includes type so that Security and Quality findings on the same line are preserved.
        """
        seen = set()
        unique = []
        for c in comments:
            # Create a signature tuple including type
            # This preserves different agent findings on the same line
            sig = (c.file, c.line, c.type, c.message[:20]) 
            if sig not in seen:
                seen.add(sig)
                unique.append(c)
        return unique

    def _generate_summary_header(self, comments: List[ReviewComment]) -> str:
        """
        Uses LLM to generate a 1-sentence executive summary of the findings.
        """
        count = len(comments)
        critical_count = len([c for c in comments if c.severity in ['Critical', 'High']])
        
        # Prepare input for the LLM
        issues_list = "\n".join([f"- [{c.type}] {c.message}" for c in comments[:10]]) # Limit to top 10 for context
        
        instruction = f"""
        Summarize these code review findings into a single professional executive summary sentence.
        Focus on the most critical issues (Security/Architectural).
        
        Findings:
        {issues_list}
        
        Output ONLY the summary sentence.
        """
        
        try:
            task = Task(
                name="Generate Summary",
                model=self.llm_model,
                agent=self.agent,
                instructions=instruction,
            )
            
            response = LinearSyncPipeline(
                name="Summary Pipeline",
                completion_message="Summary generated",
                tasks=[task],
            ).run()
            
            ai_summary = response[0]['task_output'] if isinstance(response, list) else response
            
        except Exception as e:
            logger.warning(f"Failed to generate AI summary: {e}")
            ai_summary = "Review completed."

        return f"## 🤖 Lyzr Review Report\n\n**Total Issues:** {count} | **Critical Issues:** {critical_count}\n\n> {ai_summary}\n"

    def _get_icon(self, type_: str) -> str:
        icons = {
            "Security": "🛡️",
            "Quality": "🧠",
            "Architect": "🏗️",
            "Performance": "🚀",
            "Maintainability": "🔧"
        }
        return icons.get(type_, "📝")

    def _get_severity_icon(self, severity: str) -> str:
        icons = {
            "Critical": "🔴",
            "High": "🟠",
            "Medium": "🟡",
            "Low": "🔵"
        }
        return icons.get(severity, "⚪")