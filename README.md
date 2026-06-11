# GHI Pipeline Dashboard

A real-time Kanban board for Grant's proposal pipeline. Read-write — drag cards to update status, add notes, mark proposals as no-go with reasons.

Built on FastAPI + Jinja2, reads proposal markdown files from the GHI brain repo and writes status changes back to them directly.

## Stack

- **Framework:** FastAPI, Jinja2, vanilla JS (drag-and-drop)
- **Data source:** Proposal markdown files (`proposal-*.md`) with YAML frontmatter
- **Data flow:** Reads from disk on every request, PATCH writes back to the same files
- **Sync:** Changes auto-commit and push to GitHub — the brain repo pulls them down

## Deployment

**Production:** [`https://pgb.md/pipeline`](https://pgb.md/pipeline) — served via Cloudflare Tunnel from a Proxmox LXC.

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

## Proposal format

Files follow this convention:

```yaml
---
type: proposal
slug: proposals/proposal-my-opportunity
name: Some Fund — Great Opportunity
status: watching  # or: drafting, submitted, under_review, approved, funded, no-go, rejected
funder: Funder Name
grant_id: RFA-XXX-YY-9999
deadline: 2026-12-31
amount: 500000
fit_rating: medium  # or: high, medium, low, weak
tags: [tag1, tag2]
created: 2026-01-01
updated: 2026-06-01
---
```

Valid statuses in pipeline order: `watching` → `drafting` → `no-go` → `submitted` → `under_review` → `approved` → `funded` → `rejected`.

## Routes

| Path | Description |
|---|---|
| `/` | Kanban board — drag cards between status columns |
| `/api/proposals` | JSON list of all proposals |
| `/api/proposals/:slug` | Single proposal JSON |
| `PATCH /api/proposals/:slug` | Update status (body: `{"status": "...", "reason": "..."}`) |
| `/proposal/:slug` | Detail page with full markdown rendering |
