"""
Load and parse GHI proposal markdown files from the brain.
"""

import re
import os
import logging
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import frontmatter

logger = logging.getLogger(__name__)

# Pipeline status order (MUST match Grant's canonical ordering)
# no-go sits between drafting and submitted — the go/no-go decision point.
# rejected is a hidden terminal status (hidden from default board view).
STATUS_ORDER = [
    "watching",
    "drafting",
    "no-go",
    "submitted",
    "under_review",
    "approved",
    "funded",
    "rejected",
]

# Active statuses shown on the main board (no-go and rejected are hidden in archive)
ACTIVE_STATUSES = ["watching", "drafting", "submitted", "under_review", "approved", "funded"]

STATUS_LABELS = {
    "watching": "Watching",
    "drafting": "Drafting",
    "submitted": "Submitted",
    "under_review": "Under Review",
    "approved": "Approved",
    "funded": "Funded",
    "no-go": "No-Go",
    "rejected": "Rejected",
}

STATUS_EMOJI = {
    "watching": "👀",
    "drafting": "✏️",
    "submitted": "📬",
    "under_review": "🔍",
    "approved": "✅",
    "funded": "🎉",
    "no-go": "🚫",
    "rejected": "❌",
}


class Proposal:
    def __init__(self, metadata: dict, content: str, file_path: str, filename: str):
        self.file_path = file_path
        self.filename = filename
        self.slug = metadata.get("slug", filename.replace(".md", ""))
        self.name = metadata.get("name", "Untitled Proposal")
        self.status = self._normalize_status(metadata.get("status", "watching"))
        self.funder = metadata.get("funder", "") or metadata.get("target", "")
        self.opportunity = metadata.get("opportunity", "")
        self.grant_id = metadata.get("grant_id", "")
        self.region = metadata.get("region", "")
        self.mechanism = metadata.get("mechanism", "")
        self.url = metadata.get("url", "")
        self.fit_rating = self._normalize_fit(metadata.get("fit_rating", ""))
        self.tags = metadata.get("tags", []) or []
        self.deadline = self._parse_deadline(metadata.get("deadline"))
        self.deadline_note = metadata.get("deadline_note", "")
        self.amount_raw = metadata.get("amount")
        self.amount_display = self._format_amount(metadata.get("amount"), metadata.get("amount_description", ""))
        self.amount_value = self._parse_amount_value(metadata.get("amount"))
        self.created = self._parse_date(metadata.get("created"))
        self.updated = self._parse_date(metadata.get("updated"))
        self.content = content
        self.match_reasons = metadata.get("match_reasons", []) or []
        self.challenges = metadata.get("challenges", []) or []
        self.related_to = metadata.get("related_to", []) or []
        self.no_go_reason = metadata.get("no_go_reason", "")

    @staticmethod
    def _normalize_status(status) -> str:
        if not status:
            return "watching"
        s = str(status).strip().lower()
        if s in STATUS_ORDER:
            return s
        return "watching"

    @staticmethod
    def _normalize_fit(rating) -> str:
        if not rating:
            return ""
        r = str(rating).strip().lower()
        if r in ("high", "medium", "low", "weak"):
            return r
        return ""

    @staticmethod
    def _parse_deadline(val) -> Optional[date]:
        if not val:
            return None
        try:
            if isinstance(val, date):
                return val
            return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    @staticmethod
    def _parse_date(val) -> Optional[date]:
        if not val:
            return None
        try:
            if isinstance(val, date):
                return val
            return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    def _format_amount(self, amount, description: str = "") -> str:
        if description:
            return description
        if amount is None:
            return "TBD"
        if isinstance(amount, (int, float)):
            if amount >= 1_000_000:
                return f"${amount / 1_000_000:.1f}M"
            elif amount >= 1_000:
                return f"${amount / 1_000:.0f}K"
            return f"${amount:,.0f}"
        return str(amount)

    def _parse_amount_value(self, amount) -> Optional[float]:
        """Extract a numeric value for sorting. Returns the max value if range."""
        if amount is None:
            return None
        if isinstance(amount, (int, float)):
            return float(amount)
        s = str(amount)
        # Try to extract numbers like "150-200k" or "$150,000"
        nums = re.findall(r"[\d,.]+", s.replace(",", ""))
        if nums:
            vals = [float(n) for n in nums if n]
            return max(vals) if vals else None
        return None

    @property
    def url_slug(self) -> str:
        """Derive a URL-safe slug from the stored slug or filename."""
        if "/" in self.slug:
            return self.slug.rsplit("/", 1)[-1]
        return self.slug

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status.title())

    @property
    def status_emoji(self) -> str:
        return STATUS_EMOJI.get(self.status, "")

    @property
    def days_until_deadline(self) -> Optional[int]:
        if not self.deadline:
            return None
        delta = (self.deadline - date.today()).days
        return delta

    @property
    def deadline_status(self) -> str:
        """Returns 'urgent', 'soon', 'future', or ''"""
        days = self.days_until_deadline
        if days is None:
            return ""
        if days < 0:
            return "past"
        if days <= 7:
            return "urgent"
        if days <= 30:
            return "soon"
        return "future"

    @property
    def status_index(self) -> int:
        try:
            return STATUS_ORDER.index(self.status)
        except ValueError:
            return 99

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "url_slug": self.url_slug,
            "name": self.name,
            "status": self.status,
            "status_label": STATUS_LABELS.get(self.status, self.status.title()),
            "status_emoji": STATUS_EMOJI.get(self.status, ""),
            "funder": self.funder,
            "opportunity": self.opportunity,
            "grant_id": self.grant_id,
            "region": self.region,
            "mechanism": self.mechanism,
            "fit_rating": self.fit_rating,
            "tags": self.tags,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "deadline_note": self.deadline_note,
            "days_until_deadline": self.days_until_deadline,
            "deadline_status": self.deadline_status,
            "amount_display": self.amount_display,
            "amount_value": self.amount_value,
            "created": self.created.isoformat() if self.created else None,
            "updated": self.updated.isoformat() if self.updated else None,
            "match_reasons": self.match_reasons,
            "challenges": self.challenges,
            "content": self.content,
            "filename": self.filename,
            "no_go_reason": self.no_go_reason,
            "url": self.url,
        }


def load_proposals(proposals_dir: str) -> list[Proposal]:
    """Load all proposal markdown files from the directory."""
    proposals = []
    path = Path(proposals_dir)

    if not path.exists():
        logger.warning("Proposals directory not found: %s", proposals_dir)
        return proposals

    for fpath in sorted(path.glob("proposal-*.md")):
        try:
            with open(fpath, "r") as f:
                post = frontmatter.load(f)

            # Skip draft files
            post_type = str(post.get("type", "")).strip().lower()
            if post_type == "draft":
                continue

            proposal = Proposal(post.metadata, post.content, str(fpath), fpath.name)
            proposals.append(proposal)
        except Exception as e:
            logger.error("Error loading %s: %s", fpath.name, e)

    return proposals


def get_proposal_by_slug(proposals: list[Proposal], slug: str) -> Optional[Proposal]:
    """Find a proposal by its slug. Handles both bare slugs and prefixed forms."""
    for p in proposals:
        stored = p.slug
        fname = p.filename.replace(".md", "")
        if stored == slug or stored.endswith("/" + slug) or fname == slug:
            return p
    return None


def update_proposal_status(proposals_dir: str, slug: str, new_status: str, reason: str = "") -> Proposal:
    """Update the status frontmatter field in a proposal markdown file.
    
    Optionally saves a no_go_reason to frontmatter when status is 'no-go'.
    Returns the updated Proposal object. Raises ValueError on invalid status.
    """
    if new_status not in STATUS_ORDER:
        valid = ", ".join(STATUS_ORDER)
        raise ValueError(f"Invalid status '{new_status}'. Must be one of: {valid}")
    
    # Find the file by slug
    proposals_dir = Path(proposals_dir)
    # Try direct filename
    filepath = proposals_dir / f"{slug}.md"
    if not filepath.exists():
        # Try with proposal- prefix
        filepath = proposals_dir / f"proposal-{slug}.md"
    if not filepath.exists():
        # Try finding by slug match in all proposal files
        for f in proposals_dir.glob("proposal-*.md"):
            try:
                with open(f) as fh:
                    post = frontmatter.load(fh)
                stored = str(post.get("slug", ""))
                if stored == slug or stored.endswith("/" + slug) or f.name.replace(".md", "") == slug:
                    filepath = f
                    break
            except Exception:
                continue
    
    if not filepath.exists():
        raise FileNotFoundError(f"No proposal found for slug: {slug}")
    
    # Read, modify, write
    try:
        with open(filepath) as f:
            post = frontmatter.load(f)
        
        post.metadata["status"] = new_status
        post.metadata["updated"] = date.today().isoformat()

        # Save reason when set to no-go
        if new_status == "no-go" and reason:
            # Store in frontmatter; clear if switching away from no-go
            post.metadata["no_go_reason"] = reason
        elif new_status != "no-go":
            # Clear the reason when moving out of no-go
            post.metadata.pop("no_go_reason", None)
        
        content = frontmatter.dumps(post)
        with open(filepath, "w") as f:
            f.write(content)
    except Exception as e:
        raise RuntimeError(f"Failed to update proposal {filepath.name}: {e}")
    
    # Return updated Proposal object
    return Proposal(post.metadata, post.content, str(filepath), filepath.name)


def group_by_status(proposals: list[Proposal]) -> dict:
    """Group proposals by status in pipeline order."""
    groups = {}
    for status in STATUS_ORDER:
        groups[status] = []
    for p in proposals:
        if p.status in groups:
            groups[p.status].append(p)
    return groups
