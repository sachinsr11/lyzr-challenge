import logging
from fastapi import FastAPI, BackgroundTasks, HTTPException, Request, status
import uvicorn

from src.config import settings
from src.models import RawDiffRequest, PRWebhookPayload, AnalysisReport
from src.orchestrator import ReviewOrchestrator
from src.utils import verify_webhook_signature

# --- Setup Logging ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Initialize App ---
app = FastAPI(
    title=settings.APP_NAME, 
    description="Automated Pull Request Review Agent"
)

# Global Orchestrator Instance
orchestrator = ReviewOrchestrator()

# --- 🚀 NEW: IN-MEMORY CACHE TO PREVENT DUPLICATES ---
# Stores the SHA of commits we have already analyzed.
# Format: { "repo_name/pr_number/commit_sha" }
PROCESSED_COMMITS = set()

@app.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    """
    Return a lightweight health status for service and key integrations.

    Input (sample):
    - HTTP GET /health

    Output (sample):
    {
        "status": "active",
        "service": "Lyzr PR Agent",
        "github_configured": true,
        "ai_configured": true
    }
    """
    return {
        "status": "active", 
        "service": settings.APP_NAME,
        "github_configured": bool(settings.GITHUB_TOKEN),
        "ai_configured": bool(settings.GOOGLE_API_KEY) 
    }

@app.post("/review-diff", response_model=AnalysisReport)
def manual_diff_review(request: RawDiffRequest):
    """
    Analyze a raw git diff directly and return structured review findings.

    Input (sample):
    {
        "diff_text": "diff --git a/app.py b/app.py\\n@@ -1 +1 @@\\n-print('x')\\n+print('y')"
    }

    Output (sample):
    {
        "summary": "## ...",
        "comments": [
            {
                "file": "app.py",
                "line": 1,
                "type": "Quality",
                "severity": "Low",
                "message": "...",
                "suggestion": "..."
            }
        ]
    }
    """
    if not request.diff_text.strip():
        raise HTTPException(status_code=400, detail="Diff text cannot be empty")
    try:
        report = orchestrator.process_diff_text(request.diff_text)
        return report
    except Exception as e:
        logger.error(f"Review failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/webhook", status_code=status.HTTP_200_OK)
async def github_webhook(
    request: Request, 
    background_tasks: BackgroundTasks
):
    """
        Validate and accept GitHub PR webhook events, then queue async PR review.

        Input (sample):
        - Headers:
            X-GitHub-Event: pull_request
            X-Hub-Signature-256: sha256=<hmac>
        - Body:
            {
                "action": "opened",
                "number": 42,
                "repository": {"full_name": "org/repo"},
                "pull_request": {"head": {"sha": "abc123..."}}
            }

        Output (sample):
        - {"status": "accepted"}
        - {"status": "ignored", "reason": "Duplicate event"}
        - {"status": "error", "message": "Invalid signature"}
    """
    # 0. Validate GitHub Event Type
    github_event = request.headers.get("X-GitHub-Event", "")
    if github_event != "pull_request":
        return {"status": "ignored", "reason": "Event not supported"}
    
    # 1. Verify Signature
    payload_body = await request.body()
    signature_header = request.headers.get("X-Hub-Signature-256", "")
    if settings.WEBHOOK_SECRET and not verify_webhook_signature(payload_body, signature_header):
        return {"status": "error", "message": "Invalid signature"}
    
    # 2. Parse Payload
    try:
        payload = PRWebhookPayload.model_validate_json(payload_body)
    except Exception as e:
        logger.error(f"Payload parsing failed: {e}")
        return {"status": "error", "message": "Invalid payload"}

    # 3. Filter Actions
    if payload.action not in ["opened", "synchronize"]:
        return {"status": "ignored", "reason": f"Action '{payload.action}' not supported"}

    # 4. --- 🚀 NEW: DEDUPLICATION LOGIC ---
    repo_full_name = payload.repository.get("full_name")
    pr_number = payload.number
    
    # Get the specific Commit SHA (Head)
    # This ensures we re-review if the user pushes NEW code, but ignore duplicates of the SAME code.
    head_sha = payload.pull_request.get("head", {}).get("sha", "")
    
    if not head_sha:
        logger.warning("No head SHA found in payload")
        return {"status": "ignored", "reason": "No SHA"}

    # Create a unique key for this specific state of the PR
    unique_key = f"{repo_full_name}/{pr_number}/{head_sha}"

    if unique_key in PROCESSED_COMMITS:
        logger.info(f"🛑 Skipping duplicate event for {unique_key} (Already processed)")
        return {"status": "ignored", "reason": "Duplicate event"}
    
    # Mark as processed immediately
    PROCESSED_COMMITS.add(unique_key)
    # ----------------------------------------

    # 5. Background Task
    logger.info(f"Queueing review for {repo_full_name} #{pr_number} (SHA: {head_sha[:7]})")
    background_tasks.add_task(orchestrator.process_pr, repo_full_name, pr_number)
    
    return {"status": "accepted"}


if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=getattr(settings, "HOST", "0.0.0.0"),
        port=getattr(settings, "PORT", 8000),
        reload=True
    )
