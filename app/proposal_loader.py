"""
Load and parse GHI proposal markdown files from the brain.

The vault (pbiondich/brain proposals/) is the source of truth. This module
is a derived view: it reads frontmatter and can surgically PATCH status back
without rewriting the rest of the file.
"""

import re
import logging
from dataclasses import dataclass, field
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import frontmatter

logger = logging.getLogger(__name__)

# Pipeline status order. Canonical spellings match the vault + this dashboard.
# Aliases (hyphen/underscore, closed, withdrawn, under-review) normalize here.
# no-go = team decided not to pursue; rejected = funder declined / not funded.
# Both are terminal "closed" states hidden from the default board (Show closed).
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

# Active statuses on the main board. Closed states live in archive.
ACTIVE_STATUSES = ["watching", "drafting", "submitted", "under_review", "approved", "funded"]

# Terminal statuses shown only when "Show closed" is toggled on.
ARCHIVED_STATUSES = ["no-go", "rejected"]

STATUS_LABELS = {
    "watching": "Watching",
    "drafting": "Drafting",
    "submitted": "Submitted",
    "under_review": "Under Review",
    "approved": "Approved",
    "funded": "Funded",
    "no-go": "No-Go",
    "rejected": "Not Funded",
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

# Vault and operator aliases → canonical STATUS_ORDER value.
# Do not invent new column names; map mixed spellings onto the existing board.
_STATUS_ALIASES = {
    "no_go": "no-go",
    "nogo": "no-go",
    "not_funded": "rejected",
    "notfunded": "rejected",
    "declined": "rejected",
    "unsuccessful": "rejected",
    "closed": "no-go",  # team closed without funder decision → no-go
    "archive": "no-go",
    "archived": "no-go",
    "withdrawn": "no-go",
    "withdraw": "no-go",
    "under_review": "under_review",
    "under-review": "under_review",
    "underreview": "under_review",
}

_NON_PROPOSAL_TYPES = {"draft", "brief", "reference", "note"}
_NON_PROPOSAL_PREFIXES = ("brief-", "draft-")

# Consecutive opening YAML blocks (ingest stub + vault record). Later keys win.
_LEADING_FM = re.compile(r"\A---\r?\n(.*?)\r?\n---(?:\r?\n)*", re.DOTALL)
_FM_BLOCK = re.compile(r"\A(---\r?\n)(.*?)(\r?\n---)", re.DOTALL)


@dataclass
class LoadReport:
    """Result of scanning the proposals directory."""

    proposals: list = field(default_factory=list)
    errors: list = field(default_factory=list)
    missing_dir: bool = False
    skipped: int = 0


class Proposal:
    def __init__(self, metadata: dict, content: str, file_path: str, filename: str):
        self.file_path = file_path
        self.filename = filename
        self.raw_status = metadata.get("status", "")
        self.slug = metadata.get("slug", filename.replace(".md", ""))
        self.name = self._resolve_name(metadata, content, filename)
        self.status = self._normalize_status(metadata.get("status", "watching"))
        self.funder = self._resolve_funder(metadata, self.name)
        self.opportunity = metadata.get("opportunity", "")
        self.grant_id = (
            metadata.get("grant_id", "")
            or metadata.get("reference", "")
            or ""
        )
        self.region = (
            metadata.get("geography", "")
            or metadata.get("region", "")
            or ""
        )
        self.mechanism = metadata.get("mechanism", "") or ""
        self.url = metadata.get("url", "")
        self.fit_rating = self._normalize_fit(
            metadata.get("fit_rating") or metadata.get("fit") or ""
        )
        self.tags = metadata.get("tags", []) or []
        self.deadline = self._parse_deadline(metadata.get("deadline"))
        self.deadline_note = (
            metadata.get("deadline_note", "")
            or metadata.get("deadline_notes", "")
            or ""
        )
        self.amount_raw = metadata.get("amount")
        self.amount_display = self._format_amount(
            metadata.get("amount"), metadata.get("amount_description", "")
        )
        self.amount_compact = self._compact_amount(self.amount_display, metadata.get("amount"))
        self.amount_value = self._parse_amount_value(metadata.get("amount"))
        self.created = self._parse_date(metadata.get("created"))
        self.updated = self._parse_date(metadata.get("updated"))
        self.content = content
        self.match_reasons = metadata.get("match_reasons", []) or []
        self.challenges = metadata.get("challenges", []) or []
        self.related_to = metadata.get("related_to", []) or []
        self.no_go_reason = metadata.get("no_go_reason", "")

    @staticmethod
    def _status_key(status) -> str:
        raw = str(status).strip().lower()
        return raw.replace(" ", "_").replace("-", "_")

    @classmethod
    def _normalize_status(cls, status) -> str:
        if not status:
            return "watching"
        raw = str(status).strip().lower()
        key = cls._status_key(raw)
        if key in _STATUS_ALIASES:
            return _STATUS_ALIASES[key]
        if raw in STATUS_ORDER:
            return raw
        if key in STATUS_ORDER:
            return key
        for candidate in STATUS_ORDER:
            if candidate.replace("-", "_") == key:
                return candidate
        return "watching"

    @classmethod
    def canonicalize_status(cls, status: str) -> str:
        """Public alias of _normalize_status for PATCH validation."""
        return cls._normalize_status(status)

    @staticmethod
    def _normalize_fit(rating) -> str:
        if not rating:
            return ""
        r = str(rating).strip().lower()
        aliases = {
            "strong": "high",
            "good": "high",
            "excellent": "high",
            "moderate": "medium",
            "mid": "medium",
            "poor": "low",
            "weak": "weak",
        }
        if r in aliases:
            return aliases[r]
        if r in ("high", "medium", "low", "weak"):
            return r
        return ""

    @staticmethod
    def _resolve_name(metadata: dict, content: str, filename: str) -> str:
        name = str(metadata.get("name") or "").strip()
        if name and name != "Untitled Proposal":
            return name
        title = str(metadata.get("title") or "").strip()
        # Skip auto titles like "Proposal Cdc India His Lab"
        if title and not re.match(r"^proposal\s", title, re.I):
            return title
        heading = re.search(r"^#\s+(.+)$", content or "", re.MULTILINE)
        if heading:
            return heading.group(1).strip()
        stem = filename.replace(".md", "")
        if stem.startswith("proposal-"):
            stem = stem[len("proposal-"):]
        return stem.replace("-", " ").title() or "Untitled Proposal"

    @staticmethod
    def _resolve_funder(metadata: dict, name: str) -> str:
        funder = metadata.get("funder", "") or metadata.get("target", "") or ""
        if funder:
            return str(funder)
        # Derived display only — do not write back. Many vault cards put the
        # funder in the title ("AWS — OCL Modernization") with no funder field.
        for sep in (" — ", " – ", " - "):
            if sep in name:
                prefix = name.split(sep, 1)[0].strip()
                if 1 < len(prefix) <= 48:
                    return prefix
        return ""

    @staticmethod
    def _parse_deadline(val) -> Optional[date]:
        if not val:
            return None
        try:
            if isinstance(val, datetime):
                return val.date()
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
            if isinstance(val, datetime):
                return val.date()
            if isinstance(val, date):
                return val
            return datetime.strptime(str(val)[:10], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            return None

    def _format_amount(self, amount, description: str = "") -> str:
        if description:
            return description
        if amount is None or amount == "":
            return ""
        if isinstance(amount, (int, float)):
            if amount >= 1_000_000:
                return f"${amount / 1_000_000:.1f}M"
            elif amount >= 1_000:
                return f"${amount / 1_000:.0f}K"
            return f"${amount:,.0f}"
        return str(amount)

    @staticmethod
    def _human_money(val: float) -> str:
        """Format a USD magnitude without scientific notation."""
        abs_val = abs(val)
        if abs_val >= 1_000_000_000:
            n = val / 1_000_000_000
            suffix = "B"
        elif abs_val >= 1_000_000:
            n = val / 1_000_000
            suffix = "M"
        elif abs_val >= 1_000:
            n = val / 1_000
            suffix = "K"
        else:
            return f"${val:,.0f}" if abs_val >= 1 else f"${val:g}"
        if abs(n) >= 10:
            return f"${n:.0f}{suffix}"
        text = f"{n:.1f}".rstrip("0").rstrip(".")
        return f"${text}{suffix}"

    def _compact_amount(self, display: str, amount) -> str:
        """Short card label. Long program-funding strings stay readable."""
        if not display:
            return ""
        if isinstance(amount, (int, float)):
            return self._human_money(float(amount))
        if len(display) <= 22:
            return display
        match = re.search(r"([\d,]+(?:\.\d+)?)", display)
        if not match:
            return display[:20].rstrip() + "…"
        raw = match.group(1)
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            return display[:20].rstrip() + "…"
        low = display.lower()
        # Bare "125M" / "120 million" are already in those units.
        if "," not in raw:
            if re.search(r"\bbillion\b|\b[\d.]+\s*b\b", low):
                val *= 1_000_000_000
            elif re.search(r"\bmillion\b|\b[\d.]+\s*m\b", low):
                val *= 1_000_000
            elif re.search(r"\bthousand\b|\b[\d.]+\s*k\b", low):
                val *= 1_000
        return self._human_money(val)

    def _parse_amount_value(self, amount) -> Optional[float]:
        """Extract a numeric value for sorting. Returns the max value if range."""
        if amount is None:
            return None
        if isinstance(amount, (int, float)):
            return float(amount)
        s = str(amount)
        nums = re.findall(r"[\d,.]+", s.replace(",", ""))
        if nums:
            vals = []
            for n in nums:
                try:
                    vals.append(float(n))
                except ValueError:
                    continue
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
        return (self.deadline - date.today()).days

    @property
    def deadline_is_actionable(self) -> bool:
        """True when a deadline still means 'do something' (not already sent)."""
        return self.status in ("watching", "drafting")

    @property
    def deadline_status(self) -> str:
        """Returns 'urgent', 'soon', 'future', 'past', 'closed', or 'none'."""
        days = self.days_until_deadline
        if days is None:
            return "none"
        # After the packet leaves the building, the date is history, not urgency.
        if not self.deadline_is_actionable:
            return "closed" if days <= 0 else "future"
        if days < 0:
            return "past"
        if days <= 7:
            return "urgent"
        if days <= 30:
            return "soon"
        return "future"

    @property
    def deadline_stamp(self) -> str:
        if not self.deadline:
            return ""
        if self.deadline.year != date.today().year:
            return self.deadline.strftime("%b %d, %Y")
        return self.deadline.strftime("%b %d")

    @property
    def is_forecast(self) -> bool:
        hay = " ".join(
            [
                str(self.mechanism or ""),
                str(self.deadline_note or ""),
                str(self.name or ""),
                " ".join(str(t) for t in self.tags),
            ]
        ).lower()
        return "forecast" in hay

    @property
    def region_compact(self) -> str:
        r = (self.region or "").strip()
        if not r:
            return ""
        if "(" in r:
            head = r.split("(", 1)[0].strip()
            if head:
                r = head
        if len(r) > 36:
            return r[:34].rstrip() + "…"
        return r

    @property
    def is_gpn(self) -> bool:
        hay = " ".join(
            [
                str(self.mechanism or ""),
                str(self.name or ""),
                str(self.opportunity or ""),
                " ".join(str(t) for t in self.tags),
            ]
        ).lower()
        return "gpn" in hay or "general procurement" in hay

    @property
    def is_watchlist(self) -> bool:
        """No bid window yet on an open card (typical GPN). Funded work is not a watchlist."""
        return self.deadline is None and self.deadline_is_actionable

    @property
    def mechanism_label(self) -> str:
        if self.is_forecast:
            return "Forecast"
        if not self.mechanism:
            return "GPN" if self.is_gpn else ""
        m = self.mechanism.strip()
        # Prefer a short operator chip over the full vault sentence.
        low = m.lower()
        if "gpn" in low or "general procurement" in low:
            return "GPN"
        if "nofo" in low:
            return "NOFO"
        if "lta" in low:
            return "LTA"
        if "aps" in low:
            return "APS"
        if "csa" in low or "coordination and support" in low:
            return "CSA"
        if "reoi" in low:
            return "REOI"
        if "rfp" in low:
            return "RFP"
        if "rfq" in low:
            return "RFQ"
        if len(m) > 28:
            return m[:26].rstrip() + "…"
        return m

    @property
    def search_text(self) -> str:
        parts = [
            self.name,
            self.funder,
            self.region,
            self.mechanism,
            self.grant_id,
            self.opportunity,
            " ".join(str(t) for t in self.tags),
        ]
        return " ".join(p for p in parts if p).lower()

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
            "mechanism_label": self.mechanism_label,
            "fit_rating": self.fit_rating,
            "tags": self.tags,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "deadline_note": self.deadline_note,
            "days_until_deadline": self.days_until_deadline,
            "deadline_status": self.deadline_status,
            "deadline_stamp": self.deadline_stamp,
            "is_watchlist": self.is_watchlist,
            "is_gpn": self.is_gpn,
            "is_forecast": self.is_forecast,
            "region_compact": self.region_compact,
            "amount_display": self.amount_display,
            "amount_compact": self.amount_compact,
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


def _absorb_extra_frontmatter(meta: dict, content: str) -> tuple:
    """Merge extra leading YAML blocks left in the body after the first fence.

    Some vault files have an ingest stub (`title: Proposal …`, no `name`)
    followed immediately by the real proposal frontmatter. `frontmatter.load`
    only sees the first block; later keys override.
    """
    merged = dict(meta)
    rest = content or ""
    for _ in range(3):
        match = _LEADING_FM.match(rest)
        if not match:
            break
        try:
            extra = frontmatter.loads("---\n" + match.group(1) + "\n---\n")
        except Exception:
            break
        extra_meta = dict(extra.metadata or {})
        if not extra_meta:
            break
        merged.update(extra_meta)
        rest = rest[match.end() :]
    return merged, rest


def _is_board_proposal(fpath: Path, meta: dict) -> bool:
    """Non-proposal vault files (brief-*, draft-*, type: brief/draft) stay off the board."""
    name = fpath.name.lower()
    if name.startswith(_NON_PROPOSAL_PREFIXES):
        return False
    post_type = str((meta or {}).get("type", "")).strip().lower()
    if post_type in _NON_PROPOSAL_TYPES:
        return False
    # Filename convention is the vault's board-of-record marker.
    if not name.startswith("proposal-"):
        return False
    return True


def load_proposals_report(proposals_dir: str) -> LoadReport:
    """Load proposal markdown files and report skip/error/missing-dir state."""
    report = LoadReport()
    path = Path(proposals_dir)

    if not path.exists():
        logger.warning("Proposals directory not found: %s", proposals_dir)
        report.missing_dir = True
        return report

    for fpath in sorted(path.glob("*.md")):
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                post = frontmatter.load(f)

            meta, body = _absorb_extra_frontmatter(dict(post.metadata), post.content)
            if not _is_board_proposal(fpath, meta):
                report.skipped += 1
                continue

            proposal = Proposal(meta, body, str(fpath), fpath.name)
            report.proposals.append(proposal)
        except Exception as e:
            logger.error("Error loading %s: %s", fpath.name, e)
            report.errors.append(f"{fpath.name}: {e}")

    return report


def load_proposals(proposals_dir: str) -> list:
    """Load all proposal markdown files from the directory."""
    return load_proposals_report(proposals_dir).proposals


def get_proposal_by_slug(proposals: list, slug: str) -> Optional[Proposal]:
    """Find a proposal by its slug. Handles both bare slugs and prefixed forms."""
    for p in proposals:
        stored = p.slug
        fname = p.filename.replace(".md", "")
        if stored == slug or stored.endswith("/" + slug) or fname == slug:
            return p
    return None


def _find_proposal_file(proposals_dir: Path, slug: str) -> Optional[Path]:
    filepath = proposals_dir / f"{slug}.md"
    if filepath.exists():
        return filepath
    filepath = proposals_dir / f"proposal-{slug}.md"
    if filepath.exists():
        return filepath
    for f in proposals_dir.glob("proposal-*.md"):
        try:
            with open(f, encoding="utf-8") as fh:
                post = frontmatter.load(fh)
            stored = str(post.get("slug", ""))
            if stored == slug or stored.endswith("/" + slug) or f.name.replace(".md", "") == slug:
                return f
        except Exception:
            continue
    return None


def _set_yaml_scalar(block: str, key: str, value: str) -> str:
    """Set or insert a simple YAML scalar without rewriting the rest of the block."""
    pattern = re.compile(rf"^({re.escape(key)}\s*:\s*).*$", re.MULTILINE)
    if pattern.search(block):
        return pattern.sub(lambda m: m.group(1) + value, block, count=1)
    if block and not block.endswith("\n"):
        block += "\n"
    return block + f"{key}: {value}\n"


def _remove_yaml_key(block: str, key: str) -> str:
    pattern = re.compile(rf"^{re.escape(key)}\s*:.*(?:\n|$)", re.MULTILINE)
    return pattern.sub("", block, count=1)


def _patch_frontmatter_fields(block: str, new_status: str, reason: str, today: str) -> str:
    block = _set_yaml_scalar(block, "status", new_status)
    block = _set_yaml_scalar(block, "updated", today)
    if new_status == "no-go" and reason:
        # Quote if the reason would break YAML (colon, leading special).
        safe = reason.replace("\n", " ").strip()
        if any(c in safe for c in (":", "#", "{", "}", "[", "]", ",", "&", "*", "!", "|", ">", "'", '"')):
            dumped = safe.replace("'", "''")
            safe = f"'{dumped}'"
        block = _set_yaml_scalar(block, "no_go_reason", safe)
    elif new_status != "no-go":
        block = _remove_yaml_key(block, "no_go_reason")
    return block


def _surgical_status_patch(text: str, new_status: str, reason: str, today: str) -> str:
    """Patch status/updated/no_go_reason in every consecutive leading YAML block.

    Double-frontmatter files (ingest stub + vault record) must stay in sync;
    a later load merges those blocks and the last `status` wins.
    """
    rest = text
    out = []
    patched = 0
    while patched < 4:
        lead = ""
        if patched:
            ws = re.match(r"\A(?:\r?\n)+", rest)
            if ws:
                lead = ws.group(0)
                rest = rest[ws.end() :]
        match = _FM_BLOCK.match(rest)
        if not match:
            break
        block = _patch_frontmatter_fields(match.group(2), new_status, reason, today)
        out.append(lead + match.group(1) + block + match.group(3))
        rest = rest[match.end() :]
        patched += 1
    if not patched:
        raise RuntimeError("Proposal file has no YAML frontmatter block")
    return "".join(out) + rest


def update_proposal_status(
    proposals_dir: str, slug: str, new_status: str, reason: str = ""
) -> Proposal:
    """Update the status frontmatter field in a proposal markdown file.

    Optionally saves a no_go_reason to frontmatter when status is 'no-go'.
    Writes only status / updated / no_go_reason so vault key order and
    quoting are preserved. Returns the updated Proposal object.
    """
    canonical = Proposal.canonicalize_status(new_status)
    # Reject values that collapse to watching only because they were unknown.
    if canonical not in STATUS_ORDER:
        valid = ", ".join(STATUS_ORDER)
        raise ValueError(f"Invalid status '{new_status}'. Must be one of: {valid}")
    if Proposal._status_key(new_status) not in _STATUS_ALIASES and canonical == "watching":
        raw = str(new_status).strip().lower()
        if raw not in ("watching", "") and Proposal._status_key(raw) != "watching":
            valid = ", ".join(STATUS_ORDER)
            raise ValueError(f"Invalid status '{new_status}'. Must be one of: {valid}")

    proposals_dir = Path(proposals_dir)
    filepath = _find_proposal_file(proposals_dir, slug)
    if not filepath:
        raise FileNotFoundError(f"No proposal found for slug: {slug}")

    try:
        original = filepath.read_text(encoding="utf-8")
        patched = _surgical_status_patch(
            original, canonical, reason, date.today().isoformat()
        )
        filepath.write_text(patched, encoding="utf-8")
    except RuntimeError:
        raise
    except Exception as e:
        raise RuntimeError(f"Failed to update proposal {filepath.name}: {e}") from e

    with open(filepath, encoding="utf-8") as f:
        post = frontmatter.load(f)
    meta, body = _absorb_extra_frontmatter(dict(post.metadata), post.content)
    return Proposal(meta, body, str(filepath), filepath.name)


def due_this_window(proposals: list, days: int = 30) -> list:
    """Watching/drafting bids that are overdue or due within `days`.

    Submitted / under-review / funded dates are not action items. Forecasts
    beyond the window stay in their column. GPNs with no deadline are excluded
    from this rail (they remain on the board).
    """
    due = []
    for p in proposals:
        if not p.deadline_is_actionable:
            continue
        if p.days_until_deadline is None:
            continue
        if p.is_forecast and p.days_until_deadline > days:
            continue
        if p.days_until_deadline <= days:
            due.append(p)
    due.sort(key=lambda p: (p.days_until_deadline, p.name.lower()))
    return due


def _column_sort_key(p: Proposal):
    # Dated cards first (overdue → soonest → later). Watchlist / no-deadline last.
    if p.deadline is None:
        return (1, 10**9, (p.name or "").lower())
    days = p.days_until_deadline if p.days_until_deadline is not None else 10**9
    return (0, days, (p.name or "").lower())


def group_by_status(proposals: list) -> dict:
    """Group proposals by status in pipeline order, deadline-sorted within a column."""
    groups = {status: [] for status in STATUS_ORDER}
    for p in proposals:
        if p.status in groups:
            groups[p.status].append(p)
    for status in groups:
        groups[status].sort(key=_column_sort_key)
    return groups
