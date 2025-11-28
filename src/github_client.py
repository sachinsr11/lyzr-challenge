import logging
import time
import requests
from github import Github, GithubException
from src.config import settings

logger = logging.getLogger(__name__)

# Retry configuration for rate limits
MAX_RETRIES = 3
RETRY_DELAY_SECONDS = 2

class GitHubClient:
    def __init__(self):
        """
        Initialize the GitHub client using the token from settings.
        """
        if not settings.GITHUB_TOKEN:
            logger.warning("GITHUB_TOKEN not set. GitHub operations will fail.")
            self.client = None
        else:
            self.client = Github(settings.GITHUB_TOKEN)

    def get_pr_diff(self, repo_name: str, pr_number: int) -> str:
        """
        Fetches the raw diff string for a specific Pull Request.
        Uses the diff_url property to fetch raw text.
        Includes retry logic for rate limit handling.
        """
        if not self.client:
            raise ValueError("GitHub Client not initialized")

        for attempt in range(MAX_RETRIES):
            try:
                logger.info(f"Fetching PR object: {repo_name} #{pr_number}")
                repo = self.client.get_repo(repo_name)
                pr = repo.get_pull(pr_number)
                
                # Fetch the raw diff content using the requests library
                # We use the Authorization header to ensure we can access private repos
                headers = {
                    "Authorization": f"token {settings.GITHUB_TOKEN}",
                    "Accept": "application/vnd.github.v3.diff"
                }
                
                logger.debug(f"Downloading raw diff from: {pr.diff_url}")
                response = requests.get(pr.diff_url, headers=headers, timeout= 10)
                
                # Handle rate limiting (403) with retry
                if response.status_code == 403:
                    if attempt < MAX_RETRIES - 1:
                        logger.warning(f"Rate limited. Retrying in {RETRY_DELAY_SECONDS}s... (attempt {attempt + 1}/{MAX_RETRIES})")
                        time.sleep(RETRY_DELAY_SECONDS)
                        continue
                    else:
                        logger.error("Rate limit exceeded after max retries")
                        response.raise_for_status()
                
                response.raise_for_status()
                return response.text

            except GithubException as e:
                if e.status == 403 and attempt < MAX_RETRIES - 1:
                    logger.warning(f"GitHub rate limit. Retrying in {RETRY_DELAY_SECONDS}s...")
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                logger.error(f"GitHub API Error: {e.status} - {e.data}")
                raise
            except requests.exceptions.RequestException as e:
                logger.error(f"Failed to download diff text: {e}")
                raise
            except Exception as e:
                logger.error(f"Unexpected error fetching diff: {e}")
                raise
        
        raise Exception("Failed to fetch PR diff after max retries")

    def post_comment(self, repo_name: str, pr_number: int, body: str):
        """
        Posts a general comment to the PR timeline.
        Includes retry logic for rate limit handling.
        """
        if not self.client:
            logger.error("Cannot post comment: GitHub Client not initialized")
            return

        for attempt in range(MAX_RETRIES):
            try:
                repo = self.client.get_repo(repo_name)
                pr = repo.get_pull(pr_number)
                
                # TODO: Optional - Check for existing comments by this bot to update instead of creating new ones.
                # comments = pr.get_issue_comments()
                # for c in comments: ...
                
                pr.create_issue_comment(body)
                logger.info(f"Posted comment to {repo_name} #{pr_number}")
                return  # Success, exit retry loop

            except GithubException as e:
                if e.status == 403 and attempt < MAX_RETRIES - 1:
                    logger.warning(f"GitHub rate limit. Retrying in {RETRY_DELAY_SECONDS}s...")
                    time.sleep(RETRY_DELAY_SECONDS)
                    continue
                logger.error(f"Failed to post comment: {e.status} - {e.data}")
                return
            except Exception as e:
                logger.error(f"Unexpected error posting comment: {e}")
                return