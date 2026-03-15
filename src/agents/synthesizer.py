import logging
import re
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
        """
        Initialize report-synthesis model and concise technical-writer persona.

        Input (sample):
        - None (reads settings.GOOGLE_API_KEY and SYNTHESIZER_MODEL_NAME)

        Output (sample):
        - SynthesizerAgent instance with llm_model and summary Agent.
        """
        self.llm_model = CustomLiteLLM(
            api_key=settings.GOOGLE_API_KEY,
            parameters={
                "model": settings.SYNTHESIZER_MODEL_NAME,
                "temperature": 0.2,
                "max_tokens": 200, 
            },
        )
        self.agent = Agent(
            role="Technical Writer",
            prompt_persona="You are a concise Technical Writer."
        )

    def create_report(self, comments: List[ReviewComment]) -> str:
        """
        Convert raw review comments into a deduplicated, policy-filtered markdown report.

        Input (sample):
        - comments: [ReviewComment(file="src/a.py", line=10, type="Quality", severity="High", message="...", suggestion="...")]

        Output (sample):
        - "## 🤖 Lyzr Review Report\n\n**Total Issues:** 1 | **Critical Issues:** 0\n..."
        """
        if not comments:
            return "## ✅ Lyzr Review: No Issues Found\n\nYour code looks clean!"

        for c in comments:
            try:
                c.line = int(c.line)
            except Exception:
                c.line = 0

        # 1. Pre-processing pipeline
        # Step A: Filter test files
        filtered_comments = []
        for c in comments:
            if is_test_file(c.file):
                if c.type == "Security":
                    continue
                c.severity = "Low"
            filtered_comments.append(c)

        # Step B: Domain firewall
        domain_safe_comments = self._enforce_domain_boundaries(filtered_comments)

        # Step C: Deduplication
        unique_comments = self._advanced_deduplicate(domain_safe_comments)

        # 2. Grouping & sorting
        grouped_issues = self._group_comments(unique_comments)
        
        severity_order = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        grouped_issues.sort(key=lambda x: severity_order.get(x['severity'], 4))

        # 3. Generate summary
        summary_header = self._generate_summary_header(unique_comments)
        
        report_body = "### 📊 Findings Summary\n\n"
        
        high_sev_groups = [g for g in grouped_issues if g['severity'] in ["Critical", "High"]]
        
        if high_sev_groups:
            report_body += "| Severity | Type | File | Lines | Issue |\n"
            report_body += "| :--- | :--- | :--- | :--- | :--- |\n"
            
            for g in high_sev_groups:
                icon = self._get_icon(g['type'])
                sev_icon = self._get_severity_icon(g['severity'])
                
                raw_msg = g['message'].split('\n')[0].replace("|", "-")
                short_msg = (raw_msg[:75] + '...') if len(raw_msg) > 75 else raw_msg
                
                lines_str = self._format_lines(g['lines'])
                
                report_body += f"| {sev_icon} **{g['severity']}** | {icon} {g['type']} | `{g['file']}` | {lines_str} | {short_msg} |\n"
        else:
            report_body += "*No Critical or High severity issues found. See details below.*\n"

        report_body += "\n"


        report_body += "<details>\n<summary><b>🔍 View Detailed Analysis & Code Fixes</b></summary>\n\n"
        
        files_dict = {}
        for g in grouped_issues:
            if g['file'] not in files_dict: files_dict[g['file']] = []
            files_dict[g['file']].append(g)

        for filename, groups in files_dict.items():
            report_body += f"#### 📄 `{filename}`\n"
            for g in groups:
                icon = self._get_icon(g['type'])
                sev_icon = self._get_severity_icon(g['severity'])
                
                lines_display = self._format_lines(g['lines'])
                line_prefix = "Line" if len(g['lines']) == 1 else "Lines"
                
                report_body += f"---\n"
                report_body += f"**{sev_icon} {line_prefix} {lines_display}** [{g['type']}]\n\n"
                report_body += f"**Issue:** {g['message']}\n\n"
                
                if g['suggestion']:
                    report_body += f"**Suggested Fix:**\n```python\n{g['suggestion']}\n```\n"
            report_body += "\n"

        report_body += "</details>"

        return summary_header + "\n\n" + report_body

    def _enforce_domain_boundaries(self, comments: List[ReviewComment]) -> List[ReviewComment]:
        """
        Drop findings where agent scope is violated (for example non-security agents reporting security topics).

        Input (sample):
        - comments: [ReviewComment(type="Quality", message="SQL injection risk", ...)]

        Output (sample):
        - Filtered list excluding out-of-domain comments
        """
        allowed = []
        
        # Keywords that belong ONLY to Security Agent
        security_keywords = {
            "sql injection", "xss", "csrf", "secret", "password", "api key", "credential", 
            "timing attack", "vulnerability", "unsafe", "unsanitized", "sanitize", 
            "parameterized", "raw query",
            # Expanded set for broader coverage
            "injection", "inject", "escape", "escaping", "unescaped",
            "eval", "deserialize", "deserialization",
            "tainted", "taint", "malicious", "attack", "exploit"
        }

        # False-positive phrases that should not trigger firewall
        false_ok = {
            "not vulnerable", "already sanitized", "no injection", "not an injection",
            "no vulnerability", "not exploitable", "sanitized input"
        }
        
        for c in comments:
            # UPGRADE: Regex cleaner (Fix #1)
            msg_lower = (c.message or "").lower()
            # Remove punctuation to ensure keyword matching is robust
            clean_msg = re.sub(r"[^a-z0-9 ]", " ", msg_lower)
            clean_msg = re.sub(r"\s+", " ", clean_msg).strip()

            if any(fk in clean_msg for fk in false_ok):
                allowed.append(c)
                continue

            # Rule 1: Quality/Architect cannot report security keywords
            if c.type in ["Quality", "Architect"]:
                if any(k in clean_msg for k in security_keywords):
                    logger.info(f"🔥 Firewall dropped {c.type} comment on {c.file}:{c.line} due to security keyword.")
                    continue
                    
            allowed.append(c)
        return allowed

    def _advanced_deduplicate(self, comments: List[ReviewComment]) -> List[ReviewComment]:
        """
        Deduplicate findings by enforcing one winner per file line with severity priority and security dominance.

        Input (sample):
        - comments: [Security@a.py:10 High, Quality@a.py:10 Medium, Architect@a.py:12 Low]

        Output (sample):
        - [Security@a.py:10 High, Architect@a.py:12 Low]
        """
        severity_weight = {"Critical": 0, "High": 1, "Medium": 2, "Low": 3}
        comments.sort(key=lambda x: severity_weight.get(x.severity, 4))

        final_list = []
        security_claimed_lines = set()
        all_claimed_lines = set()

        # Pass 1: Identify security claims
        for c in comments:
            if c.type == "Security":
                try:
                    ln = int(c.line)
                except Exception:
                    ln = 0
                security_claimed_lines.add((c.file, ln))

        # Pass 2: Filter and assign
        for c in comments:
            try:
                ln = int(c.line)
            except Exception:
                ln = 0
            line_key = (c.file, ln)

            # Rule A: Security dominance
            if c.type != "Security" and line_key in security_claimed_lines:
                continue

            if c.type == "Security" and line_key in all_claimed_lines:
                continue

            # Rule B: Highlander rule — one comment per line
            if line_key in all_claimed_lines:
                continue
            
            all_claimed_lines.add(line_key)
            c.line = ln
            final_list.append(c)

        return final_list

    def _group_comments(self, comments: List[ReviewComment]) -> List[Dict]:
        """
        Group similar comments by file/type/severity and normalized message signature.

        Input (sample):
        - comments: [Comment(file="a.py", message="Use parameterized query", line=3), Comment(file="a.py", message="Use parameterized query now", line=8)]

        Output (sample):
        - [{"file": "a.py", "type": "Security", "severity": "High", "message": "...", "suggestion": "...", "lines": [3, 8]}]
        """
        grouped = {}
        for c in comments:
            normalized = re.sub(r"[^a-z0-9 ]", " ", (c.message or "").lower())
            normalized = re.sub(r"\s+", " ", normalized).strip()
            words = normalized.split()
            msg_key = " ".join(words[:10])
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
        return list(grouped.values())

    def _format_lines(self, lines: List[int]) -> str:
        """
        Format line numbers for report display using compact range rules.

        Input (sample):
        - lines: [2, 3, 4, 10]

        Output (sample):
        - "2..10" (for more than 3 unique lines)
        - "2, 3" (for short lists)
        """
        lines = sorted(list(set(lines)))
        if not lines: return ""
        if len(lines) > 3:
            return f"{lines[0]}..{lines[-1]}"
        return ", ".join(map(str, lines))

    def _generate_summary_header(self, comments: List[ReviewComment]) -> str:
        """
        Generate top markdown header with issue counts and one-sentence executive summary.

        Input (sample):
        - comments: [ReviewComment(severity="Critical", message="..."), ReviewComment(severity="Low", message="...")]

        Output (sample):
        - "## 🤖 Lyzr Review Report\n\n**Total Issues:** 2 | **Critical Issues:** 1\n\n> ..."
        """
        count = len(comments)
        critical_count = len([c for c in comments if c.severity == 'Critical'])
        top_issues = comments[:5]
        issues_list = "\n".join([f"- [{c.type}] {c.message}" for c in top_issues])
        
        instruction = f"""
        You are a CTO summarizing a code review.
        Top Findings:
        {issues_list}
        Task: Write EXACTLY ONE sentence (max 20 words) summarizing the overall health.
        """
        try:
            task = Task(name="Summary", model=self.llm_model, agent=self.agent, instructions=instruction)
            response = LinearSyncPipeline(name="Summary", completion_message="Done", tasks=[task]).run()
            ai_summary = response[0]['task_output'] if isinstance(response, list) else response
            ai_summary = (ai_summary or "").replace("\n", " ").strip()
            ai_summary = re.split(r"[.!?]", ai_summary)[0][:120].strip()
        except Exception:
            ai_summary = "Review completed."

        return f"## 🤖 Lyzr Review Report\n\n**Total Issues:** {count} | **Critical Issues:** {critical_count}\n\n> {ai_summary}\n"

    def _get_icon(self, type_: str) -> str:
        """
        Map finding type to a display icon for markdown rendering.

        Input (sample):
        - type_: "Security"

        Output (sample):
        - "🛡️"
        """
        return {"Security": "🛡️", "Quality": "🧠", "Architect": "🏗️"}.get(type_, "📝")

    def _get_severity_icon(self, severity: str) -> str:
        """
        Map severity label to a visual icon used in report tables/details.

        Input (sample):
        - severity: "High"

        Output (sample):
        - "🟠"
        """
        return {"Critical": "🔴", "High": "🟠", "Medium": "🟡", "Low": "🔵"}.get(severity, "⚪")
