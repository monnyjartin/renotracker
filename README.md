# RenoTracker v1.3

RenoTracker is a self-hosted renovation tracking system built with **FastAPI + Postgres + MinIO**, designed for real-world home renovation projects.

It tracks **tasks, timelines (Gantt), expenses, documents, rooms, and dependencies** — all running in Docker.

---

## Features (Current)

### Core
- User login (bootstrap admin user)
- Projects
  - Create projects
  - Set active project
- Rooms
  - Create / edit / delete
  - Enforced unique room names per project

### Tasks
- Kanban board (Todo / Doing / Blocked / Done)
- Inline task editing
- Start date / End date planning
- Due dates
- Priority (P1–P5)
- Progress tracking (0–100%)
- Automatic status sync:
  - 0% → Todo
  - 1–99% → Doing
  - 100% → Done (sets completed_at)
- Task dependencies (Finish-to-Start)
- Gantt chart view
  - Visual timeline
  - Progress bars inside tasks
  - Dependency links
  - Drag / resize / progress updates persisted to DB

### Expenses
- Add / edit / delete expenses
- VAT, net, gross tracking
- Link expenses to:
  - Rooms
  - Tasks
- Vendor tracking
- Attach documents (receipts)

### Documents (MinIO)
- Upload files (receipts, photos, paperwork, warranties)
- Link documents to:
  - Tasks (many-to-many)
  - Expenses
  - Rooms
- Photo grouping (before / during / after)
- Tags
- Secure presigned download & preview URLs

### Dashboard
- Total spend
- Current month spend
- Open task count
- Recent expenses
- Room overview

---

## Architecture

- **FastAPI** (Python 3.12)
- **PostgreSQL** (primary datastore)
- **MinIO** (S3-compatible object storage)
- **Docker Compose** (single-host deployment)
- **SQLAlchemy ORM**
- **No Alembic** (schema auto-patching on startup)

---

## Quick Start (dadserver)

### 1️⃣ Clone / copy repo
```bash
cd ~/apps
git clone <your-repo-url> renotracker_v1_1
cd renotracker_v1_1