# Lyzr Automated PR Review Agent

## 1. Executive Summary

**What are we building?**
An intelligent, automated backend system that acts as a "Virtual Code Review Team" for GitHub Pull Requests. Instead of a simple linter that checks for missing semicolons, this system uses Large Language Models (LLMs) to understand the intent of code changes, identifying logic bugs, security vulnerabilities, and architectural flaws.

**Why are we building it?**
Manual code review is a bottleneck in software delivery.

- **Problem:** Human reviewers are often tired, miss subtle bugs, or focus too much on style (bikeshedding) rather than logic.
- **Gap:** Traditional static analysis tools (SonarQube, ESLint) cannot understand business logic or "reason" about security risks (e.g., "Is this API endpoint authorization logic flawed?").
- **Solution:** A Multi-Agent AI system where specialized agents (Security, Quality, Architect) review code in parallel and provide actionable, structured feedback.

---

## 2. Core Goals

- **Automated Analysis:** Trigger reviews automatically when a PR is opened or updated.
- **Multi-Agent Reasoning:** Simulate a team of experts (e.g., a Security Specialist, a Senior Dev, and a Software Architect) rather than a single generic AI.
- **Structured Feedback:** Output clean, JSON-structured comments mapped to specific files and line numbers—not vague paragraphs of text.
- **Hub-and-Spoke Architecture:** Use a central Orchestrator to manage data flow, ensuring scalability and preventing hallucinations.

---

## 3. Technical Architecture

**Pattern:** Supervisor/Orchestrator (Hub-and-Spoke)

**Components:**

| Layer | Description |
|-------|-------------|
| **The "Hands" (Tools Layer)** | Deterministic functions for fetching diffs from GitHub, parsing text into chunks, and posting comments back to the PR. |
| **The "Brains" (Agents Layer)** | Specialized LLM prompts and personas (Security, Quality, Architect) that analyze specific chunks of code. |
| **The "Manager" (Orchestrator Layer)** | A central service that coordinates the workflow: Fetch Diff → Split into Chunks → Assign to Agents → Aggregate Results → Post Report. |
| **The "Door" (API Layer)** | FastAPI endpoints to accept Webhooks (`/webhook`) and manual triggers (`/review-diff`). |

---

## 4. Tech Stack

| Category | Technology |
|----------|------------|
| Language | Python 3.10+ |
| Web Framework | FastAPI (Async) |
| Agent Framework | lyzr-automata (Primary) / crewai (Fallback) |
| Integration | PyGithub (GitHub API Wrapper) |
| Validation | pydantic v2 (Strict input/output schemas) |
| Deployment | Docker & Docker Compose |

---

## 5. Implementation Plan

### Phase 1: Foundation (Current Focus)

- [x] Setup Project Structure & Environment
- [ ] Implement Data Models (`src/models.py`) to enforce strict JSON schemas
- [ ] Implement Manual Diff Endpoint (`POST /review-diff`) to test logic locally without GitHub

### Phase 2: Core Logic

- [ ] Implement Diff Parser (`src/tools/diff_parser.py`) to split massive diffs into file-level chunks
- [ ] Implement Agents (`src/agents/*.py`) with specific prompts to detect Security and Logic issues
- [ ] Implement Orchestrator (`src/orchestrator.py`) to wire the parsing and agents together

### Phase 3: Integration

- [ ] Implement GitHub Client (`src/github_client.py`) to fetch real PR data
- [ ] Connect Webhook Endpoint (`POST /webhook`) to trigger the Orchestrator in the background
- [ ] Post structured comments back to the GitHub PR timeline

---

## 6. Rules for the Coding Agent

1. **Strict JSON Output:** All Agents MUST output data matching the Pydantic schemas in `src/models.py`. No raw text.

2. **Separation of Concerns:**
   - Do not put LLM logic in the API layer.
   - Do not put GitHub API calls in the Agent layer.
   - Keep orchestration in `src/orchestrator.py`.

3. **Granularity:** Process code in "chunks" (per file). Do not feed the entire repository context to the LLM to avoid token limits.

4. **Simplicity:** Start with a synchronous loop in the Orchestrator. Optimize for parallel execution (Async) only after the logic works.
