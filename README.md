# GHI Pipeline Dashboard

A Kanban board for the Regenstrief GHI Win pipeline. Read-write — drag cards to update status, filter the board, and mark proposals as no-go with reasons.

Built on FastAPI + Jinja2. It is a **derived view** of proposal markdown files in `pbiondich/brain` (`proposals/`). Status changes PATCH back onto those files; proposal records are not forked into this app.

## Stack

- **Framework:** FastAPI, Jinja2, vanilla JS (drag-and-drop)
- **Data source:** `proposal-*.md` files with YAML frontmatter (`brief-*` and `draft-*` are excluded)
- **Data flow:** Reads from disk on every request; PATCH writes `status` / `updated` / `no_go_reason` only
- **Sync:** Changes auto-commit and push to GitHub — the brain repo pulls them down

## Deployment

**Production:** [`http://pipe.pgb.md`](http://pipe.pgb.md) / [`https://pgb.md/pipeline`](https://pgb.md/pipeline) — served via Cloudflare Tunnel from a Proxmox LXC.

Auto-deploys from this repo: a cron on the server polls every 5 minutes and restarts the service on new commits.

## Local dev

```bash
# Clone
git clone git@github.com:pbiondich/ghi-pipeline-dashboard.git
cd ghi-pipeline-dashboard

# Set up
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run
PROPOSALS_DIR=/path/to/your/brain/proposals uvicorn app.main:app --reload --port 8000

# Open
open http://localhost:8000
```

The app reads from `$PROPOSALS_DIR` (defaults to `~/brain/proposals`). Point it at any directory of proposal markdown files with frontmatter.

```bash
# Tests (uses tests/fixtures, not the live vault)
PYTHONPATH=. python3 -m unittest discover -s tests -v
```

## Proposal format

Files follow this convention (vault fields the board actually reads):

```yaml
---
type: proposal
slug: proposals/proposal-my-opportunity
name: Some Fund — Great Opportunity
status: watching  # watching, drafting, submitted, under_review, approved, funded, no-go, rejected
funder: Funder Name          # or target:
grant_id: RFA-XXX-YY-9999    # or reference:
deadline: 2026-12-31         # omit for GPNs / watchlist items
deadline_note: No bid window until a specific REOI posts
geography: Ghana             # or region:
mechanism: NOFO / cooperative agreement
amount: 500000
fit_rating: medium           # or fit: (high / medium / low / weak; strong → high)
tags: [tag1, tag2]
url: https://example.org/notice
created: 2026-01-01
updated: 2026-06-01
---
```

Status aliases that must not break the board: `under-review` / `under_review`, `closed` / `withdrawn` → `no-go`. Cards with no deadline stay on the board as watchlist items (typical World Bank GPN) and sort after dated live bids.

Pipeline order: `watching` → `drafting` → `no-go` → `submitted` → `under_review` → `approved` → `funded` → `rejected`.

## Routes

| Path | Description |
|---|---|
| `/` | Kanban board — drag cards between status columns |
| `/api/proposals` | JSON list of all proposals |
| `/api/proposals/:slug` | Single proposal JSON |
| `PATCH /api/proposals/:slug` | Update status (body: `{"status": "...", "reason": "..."}`) |
| `/proposal/:slug` | Detail page with full markdown rendering |
