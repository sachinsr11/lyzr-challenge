import logging
from typing import List
from src.models import ReviewComment
from src.config import settings
from lyzr_automata import Agent, Task
from lyzr_automata.pipelines.linear_sync_pipeline import LinearSyncPipeline
from src.custom_llm import CustomLiteLLM
from src.utils import is_test_file 

logger = logging.getLogger(__name__)

class SynthesizerAgent:
    def __init__(self):
        # Issue 1 Fix: Lower max_tokens to prevent rambling
        self.llm_model = CustomLiteLLM(
            api_key=settings.GOOGLE_API_KEY,
            parameters={
                "model": settings.SYNTHESIZER_MODEL_NAME,
                "temperature": 0.2, # Lower temp for conciseness
                "max_tokens": 100,  # Strict token limit
            },
        )
        
        self.agent = Agent(
            role="Technical Writer",
            prompt_persona="You are a concise Technical Writer."
        )

    def create_report(self, comments: List[ReviewComment]) -> str:
        """
        Generates the Markdown report with aggressive filtering and cleanup.
        """
        if not comments:
            return "## ✅ Lyzr Review: No Issues Found\n\nYour code looks clean!"

        # --- PROCESS COMMENTS ---
        
        # 1. Downgrade Test Files (Issue 3)
        # We process raw comments before deduplication
        processed_comments = []
        for c in comments:
            if is_test_file(c.file):
                # Downgrade everything in tests to Low, ignore Security in tests
                if c.type == "Security":
                    continue # Skip security alerts in tests completely
                c.severity = "Low"
            processed_comments.append(c)

        # 2. Aggressive Deduplication (Issue 2 & 5)
        unique_comments = self._deduplicate(processed_comments)

        # 3. Sort by Severity
        severity_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        unique_comments.sort(key=lambda x: severity_order.get(x.severity, 4))

        # 4. Generate Concise Summary (Issue 1 & 4)
        summary_header = self._generate_summary_header(unique_comments)

        # --- FORMAT REPORT ---
        
        report_body = "### 📊 Findings Summary\n\n"
        
        # Issue 7 Fix: Table Overload
        # Only show Critical and High issues in the main table
        high_severity_issues = [c for c in unique_comments if c.severity in ["Critical", "High"]]
        
        if high_severity_issues:
            report_body += "| Severity | Type | File | Line | Issue |\n"
            report_body += "| :--- | :--- | :--- | :--- | :--- |\n"
            
            for c in high_severity_issues:
                icon = self._get_icon(c.type)
                sev_icon = self._get_severity_icon(c.severity)
                short_msg = c.message.split('\n')[0].replace("|", "-")[:80] # Truncate long messages
                report_body += f"| {sev_icon} **{c.severity}** | {icon} {c.type} | `{c.file}` | {c.line} | {short_msg} |\n"
        else:
            report_body += "*No Critical or High severity issues found. See details below.*\n"

        report_body += "\n"

        # Detailed Breakdown (Collapsible)
        report_body += "<details>\n<summary><b>🔍 View Detailed Analysis & Code Fixes</b></summary>\n\n"
        
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
        Smart Deduplication (Issue 2 & 5).
        Strategy:
        1. Normalize message (ignore case/punctuation).
        2. Line Dominance: If Security flags Line X, ignore Quality/Architect on Line X 
           (prevents 'Secret detected' showing up as a 'Magic String' quality issue).
        """
        seen_keys = set()
        security_lines = set() # (file, line)
        unique = []

        # First Pass: Collect all Security findings to establish dominance
        for c in comments:
            if c.type == "Security":
                security_lines.add((c.file, c.line))
                
        # Second Pass: Filter
        for c in comments:
            # If this is NOT security, but Security has already flagged this line, skip it
            # This reduces noise significantly
            if c.type != "Security" and (c.file, c.line) in security_lines:
                continue

            # Normalize message for exact duplicate detection
            # "Fix logic" vs "fix logic."
            norm_msg = "".join(e for e in c.message if e.isalnum()).lower()[:30]
            
            # Key: File + Line + Type (relaxed) + Normalized Message
            # Note: We rely on the Security Dominance check above to handle cross-agent dupes
            sig = (c.file, c.line, c.type, norm_msg)
            
            if sig not in seen_keys:
                seen_keys.add(sig)
                unique.append(c)
                
        return unique

    def _generate_summary_header(self, comments: List[ReviewComment]) -> str:
        """
        Generates a 1-sentence executive summary.
        """
        count = len(comments)
        critical_count = len([c for c in comments if c.severity in ['Critical', 'High']])
        
        # Issue 4 Fix: Only send top 5 issues to LLM to prevent hallucinated details
        top_issues = comments[:5]
        issues_list = "\n".join([f"- [{c.type}] {c.message}" for c in top_issues])
        
        # Issue 1 Fix: Strict Constraint in Prompt
        instruction = f"""
        You are a CTO summarizing a code review.
        
        Top Findings:
        {issues_list}
        
        Task: Write EXACTLY ONE sentence (max 20 words) summarizing the overall health.
        Do not list issues. Synthesize them.
        Example: "Critical security vulnerabilities detected in auth module requiring immediate attention."
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
            
            # Fallback cleanup if LLM ignores instructions
            ai_summary = ai_summary.replace("\n", " ").strip()
            
        except Exception as e:
            logger.warning(f"Failed to generate AI summary: {e}")
            ai_summary = "Review completed."

        return f"## 🤖 Lyzr Review Report\n\n**Total Issues:** {count} | **Critical Issues:** {critical_count}\n\n> {ai_summary}\n"

    def _get_icon(self, type_: str) -> str:
        icons = {
            "Security": "🛡️",
            "Quality": "🧠",
            "Architect": "🏗️",
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