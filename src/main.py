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
# We instantiate it once to reuse connections
orchestrator = ReviewOrchestrator()


@app.get("/health", status_code=status.HTTP_200_OK)
def health_check():
    """
    Health Check Endpoint.
    """
    # Check if critical env vars are loaded
    github_configured = bool(settings.GITHUB_TOKEN)
    google_configured = bool(settings.GOOGLE_API_KEY)
    
    return {
        "status": "active", 
        "service": settings.APP_NAME,
        "github_configured": github_configured,
        "ai_configured": google_configured 
    }


@app.post("/review-diff", response_model=AnalysisReport)
def manual_diff_review(request: RawDiffRequest):
    """
    Synchronous Endpoint for Local Testing.
    1. Accepts raw diff text in JSON body.
    2. Runs the full analysis pipeline synchronously.
    3. Returns the JSON report immediately.
    """
    logger.info("Received manual diff review request")
    
    # Validate that diff_text is not empty
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
    GitHub Webhook Listener.
    """
    # 0. Validate GitHub Event Type
    github_event = request.headers.get("X-GitHub-Event", "")
    if github_event != "pull_request":
        logger.info(f"Ignoring non-PR event: {github_event}")
        return {"status": "ignored", "reason": f"Event '{github_event}' not supported"}
    
    # 1. Get Raw Body (Critical for HMAC)
    payload_body = await request.body()
    signature_header = request.headers.get("X-Hub-Signature-256", "")

    # 2. Verify Signature
    if settings.WEBHOOK_SECRET:
        if not verify_webhook_signature(payload_body, signature_header):
            return {"status": "error", "message": "Invalid signature"}
    
    # 3. Parse Pydantic Model Manually
    try:
        payload = PRWebhookPayload.model_validate_json(payload_body)
    except Exception as e:
        logger.error(f"Payload parsing failed: {e}")
        return {"status": "error", "message": "Invalid payload structure"}

    # 4. Filter Actions
    if payload.action not in ["opened", "synchronize"]:
        return {"status": "ignored", "reason": f"Action '{payload.action}' not supported"}

    # 5. Background Task
    repo_full_name = payload.repository.get("full_name")
    pr_number = payload.number
    
    logger.info(f"Queueing review for {repo_full_name} #{pr_number}")
    background_tasks.add_task(orchestrator.process_pr, repo_full_name, pr_number)
    
    return {"status": "accepted"}


if __name__ == "__main__":
    uvicorn.run(
        "src.main:app",
        host=getattr(settings, "HOST", "0.0.0.0"),
        port=getattr(settings, "PORT", 8000),
        reload=True
    )