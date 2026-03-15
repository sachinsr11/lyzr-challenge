"""
Shared utility functions for PR Agent.
"""
import re
import json
import logging
import hashlib
from typing import Tuple, List
import hmac
from src.config import settings


logger = logging.getLogger(__name__)


def clean_json_output(raw_text: str) -> str:
    """
    Normalize LLM output by removing markdown fences and common preamble text.

    Input (sample):
    - raw_text: "```json\n[{\"file\":\"a.py\"}]\n```"

    Output (sample):
    - "[{\"file\":\"a.py\"}]"
    """
    if not raw_text:
        return "[]"
    
    # Remove markdown code blocks (```json ... ``` or ``` ... ```)
    text = re.sub(r'```json\s*', '', raw_text)
    text = re.sub(r'```\s*', '', text)
    
    # Remove common LLM preambles
    text = re.sub(r'^(Here is|Here\'s|The output is):?\s*', '', text, flags=re.IGNORECASE)
    
    # Strip whitespace
    return text.strip()


def extract_line_numbers_from_hunk(hunk_header: str) -> Tuple[int, int]:
    """
    Parse git hunk header and return old/new starting line numbers.

    Input (sample):
    - hunk_header: "@@ -10,5 +12,7 @@ def foo"

    Output (sample):
    - (10, 12)
    """
    # Match @@ -old_start,old_count +new_start,new_count @@
    match = re.search(r'@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@', hunk_header)
    if match:
        old_start = int(match.group(1))
        new_start = int(match.group(2))
        return (old_start, new_start)
    
    # Fallback for single-line changes @@ -10 +12 @@
    fallback_match = re.search(r'@@ -(\d+) \+(\d+) @@', hunk_header)
    if fallback_match:
        return (int(fallback_match.group(1)), int(fallback_match.group(2)))
    
    return (1, 1)  # Default fallback


def parse_diff_hunks(diff_content: str) -> List[dict]:
    """
    Split one file-level diff into hunk dictionaries with tracked line metadata.

    Input (sample):
    - diff_content: "@@ -1 +1 @@\n-old\n+new"

    Output (sample):
    - [{"start_line": 1, "content": "@@ -1 +1 @@\\n-old\\n+new\\n", "added_lines": [1], "removed_lines": [1]}]
    """
    hunks = []
    lines = diff_content.split('\n')
    
    current_hunk = None
    current_new_line = 1
    
    for line in lines:
        # New hunk starts
        if line.startswith('@@'):
            if current_hunk:
                hunks.append(current_hunk)
            
            old_start, new_start = extract_line_numbers_from_hunk(line)
            current_new_line = new_start
            
            current_hunk = {
                'start_line': new_start,
                'content': line + '\n',
                'added_lines': [],
                'removed_lines': []
            }
        elif current_hunk:
            current_hunk['content'] += line + '\n'
            
            # Track added lines (for reporting issues on the correct line)
            if line.startswith('+') and not line.startswith('+++'):
                current_hunk['added_lines'].append(current_new_line)
                current_new_line += 1
            elif line.startswith('-') and not line.startswith('---'):
                current_hunk['removed_lines'].append(current_new_line)
                # Don't increment for removed lines
            elif not line.startswith('\\'):  # Ignore "\ No newline at end of file"
                # Context line (no +/-)
                current_new_line += 1
    
    # Append last hunk
    if current_hunk:
        hunks.append(current_hunk)
    
    return hunks


def is_binary_file(filename: str) -> bool:
    """
    Detect whether a filename likely points to a binary/non-source asset.

    Input (sample):
    - filename: "assets/logo.png"

    Output (sample):
    - True
    """
    binary_extensions = {
        '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', 
        '.woff', '.woff2', '.ttf', '.eot', '.otf',
        '.pdf', '.zip', '.tar', '.gz', '.bz2',
        '.exe', '.dll', '.so', '.dylib',
        '.mp3', '.mp4', '.avi', '.mov'
    }
    
    ext = '.' + filename.split('.')[-1] if '.' in filename else ''
    return ext.lower() in binary_extensions


def validate_json_structure(json_str: str) -> bool:
    """
    Validate that JSON is a list of review-comment-like objects with required keys.

    Input (sample):
    - json_str: "[{\"file\":\"a.py\",\"line\":2,\"type\":\"Quality\",\"severity\":\"Low\",\"message\":\"...\"}]"

    Output (sample):
    - True
    """
    try:
        data = json.loads(json_str)
        
        # Must be a list
        if not isinstance(data, list):
            logger.warning("JSON output is not a list")
            return False
        
        # Each item should have required fields
        required_fields = {'file', 'line', 'type', 'severity', 'message'}
        for item in data:
            if not isinstance(item, dict):
                logger.warning(f"List item is not a dict: {item}")
                return False
            
            missing = required_fields - set(item.keys())
            if missing:
                logger.warning(f"Missing required fields: {missing}")
                return False
        
        return True
        
    except json.JSONDecodeError as e:
        logger.warning(f"JSON decode error: {e}")
        return False
    

def verify_webhook_signature(payload_body: bytes, signature_header: str) -> bool:
    """
    Verify GitHub webhook payload integrity using HMAC-SHA256 signature.

    Input (sample):
    - payload_body: b'{"action":"opened"}'
    - signature_header: "sha256=ab12cd..."

    Output (sample):
    - True when signature matches, otherwise False
    """
    if not settings.WEBHOOK_SECRET:
        logger.warning("WEBHOOK_SECRET not configured, skipping signature verification")
        return True  # Skip verification if no secret configured (dev mode)
    
    if not signature_header:
        return False
    
    # GitHub sends signature as "sha256=<hash>"
    if not signature_header.startswith("sha256="):
        return False
    
    expected_signature = signature_header[7:]  # Remove "sha256=" prefix
    
    # Compute HMAC-SHA256
    computed_hash = hmac.new(
        settings.WEBHOOK_SECRET.encode("utf-8"),
        payload_body,
        hashlib.sha256
    ).hexdigest()
    
    return hmac.compare_digest(computed_hash, expected_signature)

def is_test_file(filename: str) -> bool:
    """
    Detect whether a path looks like a test file for relaxed policy handling.

    Input (sample):
    - filename: "tests/test_auth.py"

    Output (sample):
    - True
    """
    fname = filename.lower()
    return (
        fname.startswith("tests/") or 
        fname.startswith("test/") or 
        fname.endswith("_test.py") or 
        fname.endswith("test.py")
    )
