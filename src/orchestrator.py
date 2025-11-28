import logging
import re
from typing import List, Dict

# Internal Modules
from src.github_client import GitHubClient
from src.models import AnalysisReport, ReviewComment
from src.utils import parse_diff_hunks, is_binary_file

# Agent Modules
from src.agents.security_agent import SecurityAgent
from src.agents.quality_agent import QualityAgent
from src.agents.architect_agent import ArchitectAgent
from src.agents.synthesizer import SynthesizerAgent

logger = logging.getLogger(__name__)

# Files to ignore during analysis (saves tokens/money)
IGNORED_EXTENSIONS = {'.lock', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.woff', '.woff2', '.ttf', '.eot'}
IGNORED_FILES = {'yarn.lock', 'package-lock.json', 'poetry.lock', 'Pipfile.lock', 'composer.lock'}


class ReviewOrchestrator:
    def __init__(self):
        """
        Initialize all Tools and Agents.
        """
        # Instantiate GitHubClient (handles its own auth via src.config)
        self.gh_client = GitHubClient()
        
        # Instantiate Agents (reuse instances for potential connection pooling)
        self.security = SecurityAgent()
        self.quality = QualityAgent()
        self.architect = ArchitectAgent()
        self.synthesizer = SynthesizerAgent()

    def process_diff_text(self, diff_text: str) -> AnalysisReport:
        """
        CORE LOGIC: Takes raw text -> Returns Analysis Report.
        This is the shared brain used by both Manual Diff endpoint and Webhook logic.
        """
        logger.info("Starting analysis of diff text...")

        # 1. Chunking
        chunks = self._split_diff_into_chunks(diff_text)
        
        # Early return if no changes found
        if not chunks:
            logger.warning("No code chunks found in diff")
            return AnalysisReport(summary="No analyzable code changes found.", comments=[])
        
        all_comments: List[ReviewComment] = []

        # 2. Agent Analysis Loop
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
                

                # SECURITY
                try:
                    sec_comments = self.security.analyze(
                        content=cleaned_hunk_content,
                        filename=filename,
                        start_line=start_line  # NEW ARG
                    )
                    all_comments.extend(sec_comments)
                except Exception as e:
                    logger.error(f"Security agent failed on {filename}: {e}")

                # QUALITY
                try:
                    qual_comments = self.quality.analyze(
                        content=cleaned_hunk_content,
                        filename=filename,
                        start_line=start_line
                    )
                    all_comments.extend(qual_comments)
                except Exception as e:
                    logger.error(f"Quality agent failed on {filename}: {e}")

                # ARCHITECT
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
        WEBHOOK ENTRY POINT: Handles the GitHub lifecycle (Fetch -> Analyze -> Post).
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
            # Post a failure comment so the user isn't waiting forever
            try:
                self.gh_client.post_comment(
                    repo_name, 
                    pr_number, 
                    "⚠️ PR Review Agent encountered an error during analysis. Please check logs."
                )
            except Exception:
                logger.error("Failed to post error comment to PR")

    def _split_diff_into_chunks(self, diff_text: str) -> List[Dict[str, str]]:
        chunks = []
        if not diff_text: return chunks

        # 1. Split by the git header
        raw_chunks = re.split(r'(diff --git)', diff_text)
        
        # re.split with capturing group keeps the delimiter, so we need to merge
        # Format will be: ['', 'diff --git', ' a/.. b/.. ...', 'diff --git', ...]
        
        current_chunk = ""
        
        for part in raw_chunks:
            if part == 'diff --git':
                # Start of a new chunk, save the previous one
                if current_chunk:
                    chunks.append(self._parse_chunk(current_chunk))
                current_chunk = part
            else:
                current_chunk += part
        
        # Append the last chunk
        if current_chunk:
            chunks.append(self._parse_chunk(current_chunk))
            
        return [c for c in chunks if c['filename'] not in ["unknown", "ignored"]]

    def _parse_chunk(self, chunk_text: str) -> dict:
        # Extract filename from diff header
        # Standard format: "diff --git a/src/main.py b/src/main.py"
        # New files: "diff --git a/dev/null b/src/new_file.py"
        # Deleted files: "diff --git a/src/old_file.py b/dev/null"
        # Renames: "diff --git a/old.py b/new.py" + "rename from/to" lines
        
        lines = chunk_text.split('\n')
        first_line = lines[0] if lines else ""
        
        # Check for rename
        is_rename = any('rename from' in line or 'rename to' in line for line in lines[:10])
        
        # Check for binary file marker
        is_binary = 'Binary files' in chunk_text or is_binary_file(first_line)
        
        # Try standard b/ path first
        match = re.search(r'diff --git a/.*? b/(.*)', first_line)
        if match:
            filename = match.group(1)
            # Handle deleted files (b/dev/null)
            if filename == 'dev/null':
                # Try to get the a/ path instead for deleted files
                a_match = re.search(r'diff --git a/(.*?) b/', first_line)
                filename = a_match.group(1) if a_match else "deleted_file"
        else:
            # Fallback: try to extract any path after a/ or b/
            fallback_match = re.search(r'diff --git.*?([ab])/(.+?)(?:\s|$)', first_line)
            if fallback_match:
                filename = fallback_match.group(2)
            else:
                filename = "unknown"
        
        # Skip ignored files
        if filename in IGNORED_FILES:
            return {'filename': 'ignored', 'content': '', 'hunks': []}
        
        # Skip by extension
        ext = '.' + filename.split('.')[-1] if '.' in filename else ''
        if ext.lower() in IGNORED_EXTENSIONS:
            return {'filename': 'ignored', 'content': '', 'hunks': []}
        
        # Skip binary files
        if is_binary:
            logger.debug(f"Skipping binary file: {filename}")
            return {'filename': 'ignored', 'content': '', 'hunks': []}
        
        # Parse hunks to extract line numbers
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