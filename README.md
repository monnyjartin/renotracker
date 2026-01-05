# RenoTracker v1 (MVP)

This is a Dockerised FastAPI + Postgres + MinIO starter app for tracking renovation projects.
V1 slice includes:
- Login (bootstrap user)
- Projects (create + set active)
- Rooms (create + list)
- Tasks (simple kanban)
- Expenses (quick add + list + filter by room/task)

## Quick start (dadserver)

1) Copy this folder to your server.

2) Edit `docker-compose.yml`:
- Change Postgres password (`POSTGRES_PASSWORD`)
- Change `SESSION_SECRET`
- Change MinIO credentials

3) Start:
```bash
docker compose up -d --build
```

4) Open:
- App: http://<dadserver-ip>:8080
- MinIO console: http://<dadserver-ip>:9001

## Default login
- Email: admin@local
- Password: admin

Change this in V2 when we add user management.

## Notes
- Tables are created automatically at startup (no Alembic yet).
- MinIO is configured but not used until the Documents/Uploads slice.
