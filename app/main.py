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
from pydantic import BaseModel, Field
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .proposal_loader import (
    load_proposals,
    load_proposals_report,
    get_proposal_by_slug,
    group_by_status,
    update_proposal_status,
    due_this_window,
    STATUS_ORDER,
    ACTIVE_STATUSES,
    ARCHIVED_STATUSES,
    STATUS_LABELS,
    STATUS_EMOJI,
)

# ── URL linkification ──────────────────────────────────────────────
# Match bare http/https URLs. Trailing sentence punctuation is kept outside
# the link. Applied only to text nodes outside existing <a> tags so we never
# double-link href attributes or already-linked anchors.
_BARE_URL_RE = re.compile(
    r'(https?://[^\s<>\"\'`]+?)'  # URL body (lazy)
    r'([.,;:!?)\]]*)'            # optional trailing punctuation (kept outside)
    r'(?=\s|<|$|"|\')'           # boundary
)
_HTML_TOKEN_RE = re.compile(r'(<[^>]+>)', re.IGNORECASE)
_A_OPEN_RE = re.compile(r'<a\b', re.IGNORECASE)
_A_CLOSE_RE = re.compile(r'</a\s*>', re.IGNORECASE)


def linkify(text) -> str:
    """Convert bare URLs in text/HTML to clickable links.

    Safe on already-rendered markdown HTML: skips tag interiors and any text
    already inside an <a>...</a> so links are not double-wrapped.
    Returns Markup for Jinja2.
    """
    if not text:
        return text

    def _link_text_node(node: str) -> str:
        def _sub(m: re.Match) -> str:
            url, trail = m.group(1), m.group(2)
            # Drop trailing sentence punctuation that snuck into the URL body
            while url and url[-1] in '.,;:!?':
                trail = url[-1] + trail
                url = url[:-1]
            if not url:
                return m.group(0)
            return (
                f'<a href="{url}" target="_blank" rel="noopener noreferrer">'
                f'{url}</a>{trail}'
            )

        return _BARE_URL_RE.sub(_sub, node)

    parts = _HTML_TOKEN_RE.split(str(text))
    out = []
    in_anchor = 0
    for part in parts:
        if part.startswith('<'):
            if _A_OPEN_RE.match(part) and not part.startswith('</'):
                in_anchor += 1
            elif _A_CLOSE_RE.match(part):
                in_anchor = max(0, in_anchor - 1)
            out.append(part)
        else:
            out.append(part if in_anchor else _link_text_node(part))
    return Markup(''.join(out))


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Where the proposal files live
PROPOSALS_DIR = os.environ.get(
    "PROPOSALS_DIR",
    "/Users/paul/brain/proposals",
)
BRAIN_DIR = str(Path(PROPOSALS_DIR).parent)


def _git_commit_push(slug: str, status: str, file_path: str = ""):
    """Fire-and-forget git commit + push after a status update."""
    try:
        add_target = ["proposals/*.md"]
        if file_path:
            try:
                rel = str(Path(file_path).resolve().relative_to(Path(BRAIN_DIR).resolve()))
                add_target = [rel]
            except ValueError:
                add_target = [file_path]
        subprocess.run(
            ["git", "-C", BRAIN_DIR, "add", "--"] + add_target,
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

# Cache-bust token for /static/* (mtime of style.css). Forces CF/Safari to
# pick up CSS after deploys instead of serving a 4h HIT of the previous build.
def _static_version() -> str:
    latest = 1
    for name in ("style.css", "board.js"):
        try:
            latest = max(latest, int((_static_dir / name).stat().st_mtime))
        except OSError:
            continue
    return str(latest)


@app.middleware("http")
async def short_cache_static(request: Request, call_next):
    """Prefer revalidation for static assets so deploys aren't stuck behind CF."""
    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "public, max-age=300, must-revalidate"
    return response


def _get_report():
    """Load proposals fresh on each request (live from synced files)."""
    return load_proposals_report(PROPOSALS_DIR)


def _get_proposals():
    return load_proposals(PROPOSALS_DIR)


def _board_context(request: Request) -> dict:
    report = _get_report()
    proposals = report.proposals
    groups = group_by_status(proposals)
    due = due_this_window(proposals, days=30)
    total_active = sum(len(groups[s]) for s in ACTIVE_STATUSES)
    total_archived = sum(len(groups[s]) for s in ARCHIVED_STATUSES)
    return {
        "groups": groups,
        "total": len(proposals),
        "total_active": total_active,
        "total_archived": total_archived,
        "due_soon": due,
        "due_overdue": sum(1 for p in due if (p.days_until_deadline or 0) < 0),
        "vault_missing": report.missing_dir,
        "load_errors": report.errors,
        "ACTIVE_STATUSES": ACTIVE_STATUSES,
        "ARCHIVED_STATUSES": ARCHIVED_STATUSES,
        "STATUS_ORDER": STATUS_ORDER,
        "STATUS_LABELS": STATUS_LABELS,
        "STATUS_EMOJI": STATUS_EMOJI,
        "today": date.today,
        "static_v": _static_version(),
    }


@app.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", _board_context(request))


@app.get("/api/proposals")
async def api_proposals():
    """JSON endpoint for programmatic access."""
    proposals = _get_proposals()
    return [p.to_dict() for p in proposals]


class StatusPatch(BaseModel):
    status: str
    reason: str = Field(default="")


@app.get("/api/proposals/{slug:path}")
async def api_proposal_detail(slug: str):
    proposals = _get_proposals()
    p = get_proposal_by_slug(proposals, slug)
    if not p:
        raise HTTPException(status_code=404, detail="Proposal not found")
    return p.to_dict()


@app.patch("/api/proposals/{slug:path}")
async def api_update_proposal(slug: str, body: StatusPatch):
    """Update a proposal field (e.g., status) and write back to the brain file."""
    reason = body.reason or ""

    try:
        # Loader canonicalizes hyphen/underscore aliases and rejects unknowns
        # so a bad status is never silently rewritten to watching.
        updated = update_proposal_status(PROPOSALS_DIR, slug, body.status, reason)
        new_status = updated.status
        threading.Thread(
            target=_git_commit_push,
            args=(slug, new_status, updated.file_path),
            daemon=True,
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
            "static_v": _static_version(),
        },
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
