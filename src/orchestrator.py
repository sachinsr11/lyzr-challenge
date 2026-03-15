import logging
import re
from typing import List, Dict

from src.github_client import GitHubClient
from src.models import AnalysisReport, ReviewComment
from src.utils import parse_diff_hunks, is_binary_file
from src.agents.security_agent import SecurityAgent
from src.agents.quality_agent import QualityAgent
from src.agents.architect_agent import ArchitectAgent
from src.agents.synthesizer import SynthesizerAgent

logger = logging.getLogger(__name__)

IGNORED_EXTENSIONS = {'.lock', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.woff', '.woff2', '.ttf', '.eot'}
IGNORED_FILES = {'yarn.lock', 'package-lock.json', 'poetry.lock', 'Pipfile.lock', 'composer.lock'}


class ReviewOrchestrator:
    def __init__(self):
        """
        Initialize GitHub tool client and all review/synthesis agents.

        Input (sample):
        - None (called as ReviewOrchestrator())

        Output (sample):
        - Initialized instance with gh_client, security, quality, architect, synthesizer.
        """
        self.gh_client = GitHubClient()
        self.security = SecurityAgent()
        self.quality = QualityAgent()
        self.architect = ArchitectAgent()
        self.synthesizer = SynthesizerAgent()

    def process_diff_text(self, diff_text: str) -> AnalysisReport:
        """
        Run full review flow on raw diff text and return aggregated report.

        Input (sample):
        - diff_text: "diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-x\n+y"

        Output (sample):
        - AnalysisReport(summary="## ...", comments=[ReviewComment(...), ...])
        """
        logger.info("Starting analysis of diff text...")

        # 1. Chunking
        chunks = self._split_diff_into_chunks(diff_text)
        
        # Early return if no changes found
        if not chunks:
            logger.warning("No code chunks found in diff")
            return AnalysisReport(summary="No analyzable code changes found.", comments=[])
        
        all_comments: List[ReviewComment] = []

        # 2. Agent analysis loop
        for chunk in chunks:
            filename = chunk['filename']
            hunks = chunk.get('hunks', [])

            if not hunks:
                logger.info(f"No hunks found in {filename}, skipping")
                continue

            logger.info(f"Analyzing file: {filename}")

            for hunk in hunks:
                hunk_content = hunk['content']
                start_line = hunk['start_line']

                cleaned_hunk_content = "\n".join(
                    line for line in hunk_content.split("\n") if not line.startswith("@@")
                )

                try:
                    sec_comments = self.security.analyze(
                        content=cleaned_hunk_content,
                        filename=filename,
                        start_line=start_line
                    )
                    all_comments.extend(sec_comments)
                except Exception as e:
                    logger.error(f"Security agent failed on {filename}: {e}")

                try:
                    qual_comments = self.quality.analyze(
                        content=cleaned_hunk_content,
                        filename=filename,
                        start_line=start_line
                    )
                    all_comments.extend(qual_comments)
                except Exception as e:
                    logger.error(f"Quality agent failed on {filename}: {e}")

                try:
                    arch_comments = self.architect.analyze(
                        content=cleaned_hunk_content,
                        filename=filename,
                        start_line=start_line
                    )
                    all_comments.extend(arch_comments)
                except Exception as e:
                    logger.error(f"Architect agent failed on {filename}: {e}")

        # 3. Synthesis
        try:
            summary = self.synthesizer.create_report(all_comments)
        except Exception as e:
            logger.error(f"Synthesizer failed: {e}")
            summary = f"Analysis completed with {len(all_comments)} findings."
        
        return AnalysisReport(summary=summary, comments=all_comments)

    def process_pr(self, repo_name: str, pr_number: int):
        """
        Fetch PR diff from GitHub, analyze it, and post summary comment back to PR.

        Input (sample):
        - repo_name: "org/repo"
        - pr_number: 42

        Output (sample):
        - None (side effects: GitHub API call to create issue comment)
        """
        try:
            logger.info(f"Processing PR #{pr_number} in {repo_name}")
            
            # Step 1: Fetch Data
            diff_text = self.gh_client.get_pr_diff(repo_name, pr_number)
            
            if not diff_text:
                logger.warning(f"No diff content for PR #{pr_number}")
                return

            # Step 2: Analyze
            report = self.process_diff_text(diff_text)

            # Step 3: Post Results
            self.gh_client.post_comment(repo_name, pr_number, report.summary)
            
            logger.info(f"Successfully posted review for PR #{pr_number}")
            
        except Exception as e:
            logger.error(f"Orchestration failed for {repo_name} #{pr_number}: {e}")
            try:
                self.gh_client.post_comment(
                    repo_name, 
                    pr_number, 
                    "⚠️ PR Review Agent encountered an error during analysis. Please check logs."
                )
            except Exception:
                logger.error("Failed to post error comment to PR")

    def _split_diff_into_chunks(self, diff_text: str) -> List[Dict[str, str]]:
        """
        Split full git diff into per-file chunks and filter ignored/unknown entries.

        Input (sample):
        - diff_text: "diff --git a/a.py b/a.py ... diff --git a/b.py b/b.py ..."

        Output (sample):
        - [
                {"filename": "a.py", "content": "...", "hunks": [...]},
                {"filename": "b.py", "content": "...", "hunks": [...]}
            ]
        """
        chunks = []
        if not diff_text: return chunks

        # 1. Split by git header and merge delimiter back into each chunk
        raw_chunks = re.split(r'(diff --git)', diff_text)
        current_chunk = ""
        
        for part in raw_chunks:
            if part == 'diff --git':
                if current_chunk:
                    chunks.append(self._parse_chunk(current_chunk))
                current_chunk = part
            else:
                current_chunk += part
        
        if current_chunk:
            chunks.append(self._parse_chunk(current_chunk))

        # 2. Filter out unresolvable and ignored entries
        return [c for c in chunks if c['filename'] not in ["unknown", "ignored"]]

    def _parse_chunk(self, chunk_text: str) -> dict:
        """
        Parse one file-level diff chunk into filename, hunk list, and file metadata.

        Input (sample):
        - chunk_text: "diff --git a/src/a.py b/src/a.py\\n@@ -1 +1 @@\\n-old\\n+new"

        Output (sample):
        - {
                "filename": "src/a.py",
                "content": "...",
                "hunks": [{"start_line": 1, "content": "...", ...}],
                "metadata": {"is_rename": false, "is_new_file": false, "is_deleted": false}
            }
        """
        # 1. Extract header info
        lines = chunk_text.split('\n')
        first_line = lines[0] if lines else ""
        is_rename = any('rename from' in line or 'rename to' in line for line in lines[:10])
        is_binary = 'Binary files' in chunk_text or is_binary_file(first_line)

        # 2. Resolve filename from diff header
        match = re.search(r'diff --git a/.*? b/(.*)', first_line)
        if match:
            filename = match.group(1)
            if filename == 'dev/null':
                a_match = re.search(r'diff --git a/(.*?) b/', first_line)
                filename = a_match.group(1) if a_match else "deleted_file"
        else:
            fallback_match = re.search(r'diff --git.*?([ab])/(.+?)(?:\s|$)', first_line)
            if fallback_match:
                filename = fallback_match.group(2)
            else:
                filename = "unknown"

        # 3. Skip ignored, extension-blocked, and binary files
        if filename in IGNORED_FILES:
            return {'filename': 'ignored', 'content': '', 'hunks': []}
        
        ext = '.' + filename.split('.')[-1] if '.' in filename else ''
        if ext.lower() in IGNORED_EXTENSIONS:
            return {'filename': 'ignored', 'content': '', 'hunks': []}
        
        if is_binary:
            logger.debug(f"Skipping binary file: {filename}")
            return {'filename': 'ignored', 'content': '', 'hunks': []}

        # 4. Parse hunks and build result
        hunks = parse_diff_hunks(chunk_text)
        
        metadata = {
            'is_rename': is_rename,
            'is_new_file': '/dev/null' in first_line and 'a/dev/null' in first_line,
            'is_deleted': '/dev/null' in first_line and 'b/dev/null' in first_line
        }
            
        return {
            'filename': filename, 
            'content': chunk_text,
            'hunks': hunks,
            'metadata': metadata
        }
