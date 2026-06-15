"""
GHI Pipeline Dashboard — FastAPI application.
Reads proposal markdown files from the brain and serves a pipeline dashboard.
"""

import os
import re
import logging
import subprocess
import threading
from datetime import date
from pathlib import Path

import markdown as md_lib
from markupsafe import Markup
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .proposal_loader import load_proposals, get_proposal_by_slug, group_by_status, update_proposal_status, STATUS_ORDER, ACTIVE_STATUSES, STATUS_LABELS, STATUS_EMOJI

# ── URL linkification ──────────────────────────────────────────────
# Match bare http/https URLs not already inside markdown link syntax [text](url)
_BARE_URL_RE = re.compile(r'(?<!\()(https?://[^\s\)"<>`]+)(?![\)])')

def linkify(text) -> str:
    """Convert bare URLs in text to clickable HTML links.

    Returns a Markup object (safe for Jinja2 rendering without escaping).
    """
    if not text:
        return text
    result = _BARE_URL_RE.sub(
        r'<a href="\1" target="_blank" rel="noopener noreferrer">\1</a>',
        str(text),
    )
    return Markup(result)


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Where the proposal files live
PROPOSALS_DIR = os.environ.get(
    "PROPOSALS_DIR",
    "/Users/paul/brain/proposals",
)
BRAIN_DIR = str(Path(PROPOSALS_DIR).parent)

def _git_commit_push(slug: str, status: str):
    """Fire-and-forget git commit + push after a status update."""
    try:
        subprocess.run(
            ["git", "-C", BRAIN_DIR, "add", "proposals/*.md"],
            capture_output=True, timeout=10,
        )
        r = subprocess.run(
            ["git", "-C", BRAIN_DIR, "diff", "--staged", "--quiet"],
            capture_output=True, timeout=10,
        )
        if r.returncode != 0:
            subprocess.run(
                ["git", "-C", BRAIN_DIR, "commit", "-m",
                 f"auto: {slug} status → {status} [dashboard]"],
                capture_output=True, timeout=10,
            )
            # Pull before push to avoid rejection
            subprocess.run(
                ["git", "-C", BRAIN_DIR, "pull", "--ff-only", "origin", "main"],
                capture_output=True, timeout=30,
            )
            subprocess.run(
                ["git", "-C", BRAIN_DIR, "push", "origin", "main"],
                capture_output=True, timeout=30,
            )
            logger.info("Pushed status update for %s (%s)", slug, status)
    except Exception as e:
        logger.warning("Git sync failed for %s: %s", slug, e)

app = FastAPI(title="GHI Pipeline Dashboard")

# Mount static files
_static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")

# Templates
_templates_dir = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(_templates_dir))
templates.env.filters["linkify"] = linkify


def _get_proposals():
    """Load proposals fresh on each request (live from synced files)."""
    return load_proposals(PROPOSALS_DIR)


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    proposals = _get_proposals()
    groups = group_by_status(proposals)
    total_active = sum(len(groups[s]) for s in ACTIVE_STATUSES)
    total_archived = sum(len(groups[s]) for s in ["no-go", "rejected"])
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "groups": groups,
            "total": len(proposals),
            "total_active": total_active,
            "total_archived": total_archived,
            "ACTIVE_STATUSES": ACTIVE_STATUSES,
            "STATUS_ORDER": STATUS_ORDER,
            "STATUS_LABELS": STATUS_LABELS,
            "STATUS_EMOJI": STATUS_EMOJI,
            "today": date.today,
        },
    )


@app.get("/api/proposals")
async def api_proposals():
    """JSON endpoint for programmatic access."""
    proposals = _get_proposals()
    return [p.to_dict() for p in proposals]


@app.get("/api/proposals/{slug}")
async def api_proposal_detail(slug: str):
    proposals = _get_proposals()
    p = get_proposal_by_slug(proposals, slug)
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return p.to_dict()


@app.patch("/api/proposals/{slug}")
async def api_update_proposal(slug: str, body: dict):
    """Update a proposal field (e.g., status) and write back to the brain file."""
    if "status" not in body:
        raise HTTPException(status_code=400, detail="Request body must include 'status' field")
    
    new_status = body["status"]
    reason = body.get("reason", "")
    
    try:
        updated = update_proposal_status(PROPOSALS_DIR, slug, new_status, reason)
        # Fire-and-forget git sync in background thread
        threading.Thread(
            target=_git_commit_push, args=(slug, new_status), daemon=True
        ).start()
        return updated.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        logger.error("Failed to update proposal %s: %s", slug, e)
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/proposal/{slug}", response_class=HTMLResponse)
async def proposal_detail(request: Request, slug: str):
    proposals = _get_proposals()
    p = get_proposal_by_slug(proposals, slug)
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")

    content_html = md_lib.markdown(
        p.content,
        extensions=["fenced_code", "tables", "nl2br"],
    )

    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "proposal": p,
            "content_html": content_html,
            "STATUS_ORDER": STATUS_ORDER,
            "STATUS_LABELS": STATUS_LABELS,
            "STATUS_EMOJI": STATUS_EMOJI,
            "today": date.today,
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
