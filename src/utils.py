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
    Strip markdown code blocks and other LLM artifacts from JSON output.
    
    Args:
        raw_text: Raw text from LLM that should contain JSON
        
    Returns:
        Cleaned JSON string ready for parsing
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
    Extract starting line numbers from a diff hunk header.
    
    Args:
        hunk_header: String like "@@ -10,5 +12,7 @@ function_name"
        
    Returns:
        Tuple of (old_start_line, new_start_line)
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
    Parse diff content into individual hunks with line number tracking.
    
    Args:
        diff_content: Raw diff text for a single file
        
    Returns:
        List of dicts with 'start_line', 'content', 'added_lines', 'removed_lines'
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
    """Check if a file is likely binary based on extension."""
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
    Validate that JSON string is parseable and matches expected structure.
    
    Args:
        json_str: JSON string to validate
        
    Returns:
        True if valid, False otherwise
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
    Verify GitHub webhook signature using HMAC-SHA256.
    Returns True if signature is valid, False otherwise.
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
    Detects if a file is a test file to allow relaxed rules.
    """
    fname = filename.lower()
    return (
        fname.startswith("tests/") or 
        fname.startswith("test/") or 
        fname.endswith("_test.py") or 
        fname.endswith("test.py")
    )
