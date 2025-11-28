import logging
from typing import List, Dict
from src.models import ReviewComment
from src.config import settings
from lyzr_automata import Agent, Task
from lyzr_automata.pipelines.linear_sync_pipeline import LinearSyncPipeline
from src.custom_llm import CustomLiteLLM
from src.utils import is_test_file

logger = logging.getLogger(__name__)

class SynthesizerAgent:
    def __init__(self):
        self.llm_model = CustomLiteLLM(
            api_key=settings.GOOGLE_API_KEY,
            parameters={
                "model": settings.SYNTHESIZER_MODEL_NAME,
                "temperature": 0.2,
                "max_tokens": 200, # Concise summary
            },
        )
        
        self.agent = Agent(
            role="Technical Writer",
            prompt_persona="You are a concise Technical Writer."
        )

    def create_report(self, comments: List[ReviewComment]) -> str:
        """
        Generates the Markdown report with Grouping and Deduplication.
        """
        if not comments:
            return "## ✅ Lyzr Review: No Issues Found\n\nYour code looks clean!"

        # 1. Downgrade Test Files
        processed_comments = []
        for c in comments:
            if is_test_file(c.file):
                if c.type == "Security": continue 
                c.severity = "Low"
            processed_comments.append(c)

        # 2. Smart Deduplication
        unique_comments = self._deduplicate(processed_comments)

        # 3. Group Identical Comments (The "Human" Touch)
        grouped_issues = self._group_comments(unique_comments)

        # 4. Sort by Severity
        severity_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        grouped_issues.sort(key=lambda x: severity_order.get(x['severity'], 4))

        # 5. Generate Summary
        summary_header = self._generate_summary_header(unique_comments)

        # --- FORMAT REPORT ---
        report_body = "### 📊 Findings Summary\n\n"
        
        # Table: Only High/Critical Groups
        high_sev_groups = [g for g in grouped_issues if g['severity'] in ["Critical", "High"]]
        
        if high_sev_groups:
            report_body += "| Severity | Type | File | Lines | Issue |\n"
            report_body += "| :--- | :--- | :--- | :--- | :--- |\n"
            
            for g in high_sev_groups:
                icon = self._get_icon(g['type'])
                sev_icon = self._get_severity_icon(g['severity'])
                short_msg = g['message'].split('\n')[0].replace("|", "-")[:60]
                lines_str = ", ".join(map(str, g['lines']))
                # Truncate lines if too many
                if len(g['lines']) > 3: lines_str = f"{g['lines'][0]}..{g['lines'][-1]}"
                
                report_body += f"| {sev_icon} **{g['severity']}** | {icon} {g['type']} | `{g['file']}` | {lines_str} | {short_msg} |\n"
        else:
            report_body += "*No Critical or High severity issues found. See details below.*\n"

        report_body += "\n"

        # Detailed Breakdown
        report_body += "<details>\n<summary><b>🔍 View Detailed Analysis & Code Fixes</b></summary>\n\n"
        
        # Organize groups by file for display
        files_dict = {}
        for g in grouped_issues:
            if g['file'] not in files_dict: files_dict[g['file']] = []
            files_dict[g['file']].append(g)

        for filename, groups in files_dict.items():
            report_body += f"#### 📄 `{filename}`\n"
            for g in groups:
                icon = self._get_icon(g['type'])
                sev_icon = self._get_severity_icon(g['severity'])
                
                # Format lines: "Line 10" or "Lines 10, 12, 15"
                lines_display = ", ".join(map(str, g['lines']))
                line_prefix = "Line" if len(g['lines']) == 1 else "Lines"
                
                report_body += f"---\n"
                report_body += f"**{sev_icon} {line_prefix} {lines_display}** [{g['type']}]\n\n"
                report_body += f"**Issue:** {g['message']}\n\n"
                
                if g['suggestion']:
                    report_body += f"**Suggested Fix:**\n```python\n{g['suggestion']}\n```\n"
            report_body += "\n"

        report_body += "</details>"

        return summary_header + "\n\n" + report_body

    def _deduplicate(self, comments: List[ReviewComment]) -> List[ReviewComment]:
        """
        Removes exact duplicates and prevents Quality/Architect from reporting 
        on lines where Security has already flagged an issue.
        """
        seen_keys = set()
        security_lines = set() 
        unique = []

        # Pass 1: Identify Security Lines
        for c in comments:
            if c.type == "Security":
                security_lines.add((c.file, c.line))
                
        # Pass 2: Filter
        for c in comments:
            # If Security flagged this line, ignore Quality/Architect opinions on it
            if c.type != "Security" and (c.file, c.line) in security_lines:
                continue

            norm_msg = "".join(e for e in c.message if e.isalnum()).lower()[:30]
            sig = (c.file, c.line, c.type, norm_msg)
            
            if sig not in seen_keys:
                seen_keys.add(sig)
                unique.append(c)
                
        return unique

    def _group_comments(self, comments: List[ReviewComment]) -> List[Dict]:
        """
        Merges comments that have the same File, Type, Severity, and Message content.
        Returns a list of dicts: {'file': str, 'lines': [int], 'message': str...}
        """
        grouped = {}
        
        for c in comments:
            # Key: (File, Type, Severity, Normalized Message)
            # We use a normalized message to group similar findings
            msg_key = c.message.strip()
            key = (c.file, c.type, c.severity, msg_key)
            
            if key not in grouped:
                grouped[key] = {
                    'file': c.file,
                    'type': c.type,
                    'severity': c.severity,
                    'message': c.message,
                    'suggestion': c.suggestion,
                    'lines': []
                }
            grouped[key]['lines'].append(c.line)
            
        # Convert values to list
        return list(grouped.values())

    def _generate_summary_header(self, comments: List[ReviewComment]) -> str:
        count = len(comments)
        critical_count = len([c for c in comments if c.severity in ['Critical', 'High']])
        
        # Only use unique issues for summary generation to save tokens
        top_issues = comments[:5]
        issues_list = "\n".join([f"- [{c.type}] {c.message}" for c in top_issues])
        
        instruction = f"""
        You are a CTO summarizing a code review.
        Top Findings:
        {issues_list}
        Task: Write EXACTLY ONE sentence (max 20 words) summarizing the overall health.
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