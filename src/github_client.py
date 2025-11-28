import logging
import requests
from github import Github, GithubException
from src.config import settings

logger = logging.getLogger(__name__)

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
        """
        if not self.client:
            raise ValueError("GitHub Client not initialized")

        try:
            logger.info(f"Fetching PR object: {repo_name} #{pr_number}")
            repo = self.client.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
            
            # Fetch the raw diff content using the requests library
            headers = {
                "Authorization": f"token {settings.GITHUB_TOKEN}",
                "Accept": "application/vnd.github.v3.diff"
            }
            
            logger.debug(f"Downloading raw diff from: {pr.diff_url}")
            response = requests.get(pr.diff_url, headers=headers, timeout=10)
            response.raise_for_status()
            
            return response.text

        except Exception as e:
            logger.error(f"Failed to fetch diff for {repo_name} #{pr_number}: {e}")
            raise

    def post_comment(self, repo_name: str, pr_number: int, body: str):
        """
        Posts a general comment to the PR timeline.
        No retry logic - simple fire and forget.
        """
        if not self.client:
            logger.error("Cannot post comment: GitHub Client not initialized")
            return

        try:
            repo = self.client.get_repo(repo_name)
            pr = repo.get_pull(pr_number)
            
            pr.create_issue_comment(body)
            logger.info(f"Successfully posted comment to {repo_name} #{pr_number}")

        except GithubException as e:
            logger.error(f"GitHub API Error posting comment: {e.status} - {e.data}")
        except Exception as e:
            logger.error(f"Unexpected error posting comment: {e}")