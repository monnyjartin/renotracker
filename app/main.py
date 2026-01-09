from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from uuid import uuid4
import logging
import traceback

from fastapi import FastAPI, Request, Depends, Form, UploadFile, File
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from sqlalchemy.orm import Session
from sqlalchemy import text, func
from sqlalchemy.exc import IntegrityError

from .config import settings
from .db import engine, Base, get_db
from .models import (
    User,
    Project,
    Room,
    Expense,
    Task,
    Document,
    DocumentTask,
)
from .security import verify_password
from .auth import get_current_user, login_user, logout_user
from .seed import ensure_admin_user
from .storage import ensure_bucket_exists, upload_bytes, presigned_get_url, s3_enabled


log = logging.getLogger("renotracker")
logging.basicConfig(level=logging.INFO)

app = FastAPI(title="RenoTracker v1.2")
app.add_middleware(SessionMiddleware, secret_key=settings.session_secret)

from fastapi.responses import PlainTextResponse

@app.exception_handler(RuntimeError)
async def runtime_redirect_handler(request: Request, exc: RuntimeError):
    msg = str(exc)
    if msg.startswith("AUTH_REQUIRED_REDIRECT:"):
        url = msg.split(":", 1)[1]
        return _redirect(url)
    return PlainTextResponse(msg, status_code=500)


# Serve /static/styles.css from app/static/styles.css
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")


def utcnow() -> datetime:
    return datetime.utcnow()


def _set_if_attr(obj, attr: str, value) -> None:
    """Set obj.attr = value only if the SQLAlchemy model defines that attribute."""
    if hasattr(obj, attr):
        setattr(obj, attr, value)


def _redirect(url: str) -> RedirectResponse:
    return RedirectResponse(url=url, status_code=303)


def _clean_str(v) -> str:
    return (v or "").strip()


def _is_unique_violation(ex: Exception) -> bool:
    msg = str(ex).lower()
    return ("duplicate key value violates unique constraint" in msg) or ("unique" in msg and "violat" in msg)


def _log_exception(prefix: str, ex: Exception) -> None:
    log.error("%s: %s", prefix, ex)
    log.error(traceback.format_exc())


def _require_user_and_project(request: Request, db: Session) -> User:
    user = get_current_user(request, db)
    if not user:
        # FastAPI can't raise a Response cleanly from deep helpers.
        # Raise a RuntimeError that our exception handler can convert to a redirect.
        raise RuntimeError("AUTH_REQUIRED_REDIRECT:/login")

    if not user.active_project_id:
        raise RuntimeError("AUTH_REQUIRED_REDIRECT:/projects")

    return user



def _norm_doc_type(v: str) -> str:
    v = _clean_str(v).lower() or "receipt"
    if v not in ("receipt", "photo", "warranty", "paperwork", "recipe"):
        return "receipt"
    return v


def _norm_photo_group(doc_type: str, v: str):
    if doc_type != "photo":
        return None
    pg = _clean_str(v).lower() or "before"
    if pg not in ("before", "during", "after"):
        return "before"
    return pg


def _norm_tags(v: str):
    raw = _clean_str(v)
    if not raw:
        return None
    parts = [p.strip() for p in raw.replace(";", ",").split(",")]
    parts = [p for p in parts if p]
    if not parts:
        return None
    seen = set()
    out = []
    for p in parts:
        k = p.lower()
        if k not in seen:
            seen.add(k)
            out.append(p)
    return ",".join(out)


def _parse_task_ids(task_ids):
    if task_ids is None:
        return []
    vals = task_ids if isinstance(task_ids, list) else [task_ids]
    cleaned = []
    for v in vals:
        s = _clean_str(str(v))
        if s:
            cleaned.append(s)
    seen = set()
    out = []
    for x in cleaned:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


# ------------------------------------------------
# Startup / schema helpers (no Alembic yet)
# ------------------------------------------------
def ensure_schema() -> None:
    """
    Lightweight schema patching for v1.x.
    Adds / fixes (if missing):
      - documents.photo_group
      - documents.tags
      - document_tasks join table
      - expenses.vendor

    ALSO PATCHES timestamp defaults where DB has NOT NULL updated_at but ORM omits it:
      - rooms.updated_at default now()
      - tasks.updated_at default now()
      - expenses.updated_at default now()
      - projects.updated_at default now()
      - documents.updated_at default now()
    """
    try:
        with engine.begin() as conn:
            # --- helper: set default now() on updated_at if column exists and has no default
            def ensure_updated_at_default(table: str):
                # Column exists?
                res = conn.execute(text("""
                    SELECT column_default
                    FROM information_schema.columns
                    WHERE table_name = :t AND column_name = 'updated_at'
                    LIMIT 1
                """), {"t": table})
                row = res.first()
                if row is None:
                    return

                col_default = row[0]
                # If updated_at exists but has no default, set one
                if col_default is None:
                    conn.execute(text(f"ALTER TABLE {table} ALTER COLUMN updated_at SET DEFAULT now()"))

                # Backfill any nulls (if they exist) to satisfy NOT NULL constraints
                # Prefer created_at if present, else now()
                has_created = conn.execute(text("""
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = :t AND column_name = 'created_at'
                    LIMIT 1
                """), {"t": table}).first() is not None

                if has_created:
                    conn.execute(text(f"""
                        UPDATE {table}
                        SET updated_at = COALESCE(updated_at, created_at, now())
                        WHERE updated_at IS NULL
                    """))
                else:
                    conn.execute(text(f"""
                        UPDATE {table}
                        SET updated_at = COALESCE(updated_at, now())
                        WHERE updated_at IS NULL
                    """))

            # documents.photo_group
            res = conn.execute(text("""
                SELECT 1
                FROM information_schema.columns
                WHERE table_name='documents' AND column_name='photo_group'
                LIMIT 1
            """))
            if res.first() is None:
                conn.execute(text("ALTER TABLE documents ADD COLUMN photo_group VARCHAR(20)"))

            # documents.tags
            res = conn.execute(text("""
                SELECT 1
                FROM information_schema.columns
                WHERE table_name='documents' AND column_name='tags'
                LIMIT 1
            """))
            if res.first() is None:
                conn.execute(text("ALTER TABLE documents ADD COLUMN tags TEXT"))

            # document_tasks join table
            res = conn.execute(text("""
                SELECT 1
                FROM information_schema.tables
                WHERE table_name='document_tasks'
                LIMIT 1
            """))
            if res.first() is None:
                conn.execute(text("""
                    CREATE TABLE document_tasks (
                        document_id VARCHAR NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
                        task_id     VARCHAR NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                        created_at  TIMESTAMP NULL,
                        PRIMARY KEY (document_id, task_id)
                    )
                """))

            # expenses.vendor
            res = conn.execute(text("""
                SELECT 1
                FROM information_schema.columns
                WHERE table_name='expenses' AND column_name='vendor'
                LIMIT 1
            """))
            if res.first() is None:
                conn.execute(text("ALTER TABLE expenses ADD COLUMN vendor VARCHAR(255)"))

            # Patch updated_at defaults so INSERTs succeed even if ORM doesn't include updated_at
            for tbl in ("rooms", "tasks", "expenses", "projects", "documents"):
                ensure_updated_at_default(tbl)

    except Exception:
        # Do not block startup
        pass


@app.on_event("startup")
def startup():
    Base.metadata.create_all(bind=engine)
    ensure_schema()
    ensure_bucket_exists()

    from .db import SessionLocal
    db = SessionLocal()
    try:
        ensure_admin_user(db)
    finally:
        db.close()


# ------------------------------------------------
# Auth
# ------------------------------------------------
@app.get("/login", response_class=HTMLResponse)
def login_get(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.post("/login")
def login_post(
    request: Request,
    db: Session = Depends(get_db),
    email: str = Form(...),
    password: str = Form(...),
):
    user = db.query(User).filter(User.email == email.lower().strip()).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid credentials."},
            status_code=401,
        )
    login_user(request, user)
    return _redirect("/dashboard")


@app.post("/logout")
def logout_post(request: Request):
    logout_user(request)
    return _redirect("/login")


# ------------------------------------------------
# Home / Dashboard
# ------------------------------------------------
@app.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return _redirect("/login")

    if not user.active_project_id:
        proj = (
            db.query(Project)
            .filter(Project.is_archived == False)
            .order_by(Project.created_at.desc())
            .first()
        )
        if proj:
            user.active_project_id = proj.id
            db.commit()

    return _redirect("/dashboard")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return _redirect("/login")

    active_project = db.get(Project, user.active_project_id) if user.active_project_id else None
    if not active_project:
        return _redirect("/projects")

    total_spend_rows = (
        db.query(Expense.gross_amount)
        .filter(Expense.project_id == active_project.id)
        .all()
    )
    total_spend = sum(float(x[0]) for x in total_spend_rows) if total_spend_rows else 0.0

    month_start = date.today().replace(day=1)
    month_rows = (
        db.query(Expense.gross_amount)
        .filter(
            Expense.project_id == active_project.id,
            Expense.purchase_date >= month_start
        )
        .all()
    )
    month_spend = sum(float(x[0]) for x in month_rows) if month_rows else 0.0

    open_tasks = db.query(Task).filter(
        Task.project_id == active_project.id,
        Task.status != "done"
    ).count()

    recent_expenses = (
        db.query(Expense)
        .filter(Expense.project_id == active_project.id)
        .order_by(Expense.purchase_date.desc(), Expense.created_at.desc())
        .limit(10)
        .all()
    )

    rooms = db.query(Room).filter(Room.project_id == active_project.id).order_by(Room.name.asc()).all()

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "user": user,
            "active_project": active_project,
            "total_spend": total_spend,
            "month_spend": month_spend,
            "open_tasks": open_tasks,
            "recent_expenses": recent_expenses,
            "rooms": rooms,
        },
    )


# -----------------
# Projects
# -----------------
@app.get("/projects", response_class=HTMLResponse)
def projects_list(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return _redirect("/login")

    projects = db.query(Project).order_by(Project.created_at.desc()).all()
    return templates.TemplateResponse("projects.html", {"request": request, "user": user, "projects": projects})


@app.post("/projects/create")
def projects_create(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    description: str = Form(""),
):
    user = get_current_user(request, db)
    if not user:
        return _redirect("/login")

    now = utcnow()
    p = Project(
        id=str(uuid4()),
        name=_clean_str(name),
        description=_clean_str(description) or None,
        currency="GBP",
    )
    _set_if_attr(p, "created_at", now)
    _set_if_attr(p, "updated_at", now)

    db.add(p)
    db.commit()

    user.active_project_id = p.id
    db.commit()
    return _redirect("/dashboard")


@app.post("/projects/{project_id}/set-active")
def projects_set_active(project_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return _redirect("/login")

    p = db.get(Project, project_id)
    if not p or p.is_archived:
        return _redirect("/projects")

    user.active_project_id = p.id
    db.commit()
    return _redirect("/dashboard")


# -----------------
# Rooms
# -----------------
@app.get("/rooms", response_class=HTMLResponse)
def rooms_list(request: Request, db: Session = Depends(get_db)):
    user = _require_user_and_project(request, db)

    active_project = db.get(Project, user.active_project_id)
    rooms = db.query(Room).filter(Room.project_id == active_project.id).order_by(Room.name.asc()).all()

    return templates.TemplateResponse(
        "rooms.html",
        {
            "request": request,
            "user": user,
            "active_project": active_project,
            "rooms": rooms,
            "err": request.query_params.get("err") or "",
        },
    )


@app.post("/rooms/create")
def rooms_create(
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    floor: str = Form(""),
    status: str = Form(""),
):
    user = _require_user_and_project(request, db)

    room_name = _clean_str(name)
    if not room_name:
        return _redirect("/rooms?err=missing_name")

    exists = (
        db.query(Room.id)
        .filter(Room.project_id == user.active_project_id)
        .filter(func.lower(Room.name) == room_name.lower())
        .first()
    )
    if exists:
        return _redirect("/rooms?err=duplicate")

    now = utcnow()
    r = Room(
        id=str(uuid4()),
        project_id=user.active_project_id,
        name=room_name,
        floor=_clean_str(floor) or None,
        status=_clean_str(status) or None,
    )
    _set_if_attr(r, "created_at", now)
    _set_if_attr(r, "updated_at", now)

    db.add(r)
    try:
        db.commit()
    except IntegrityError as ex:
        db.rollback()
        _log_exception("rooms_create IntegrityError", ex)
        if _is_unique_violation(ex):
            return _redirect("/rooms?err=duplicate")
        return _redirect("/rooms?err=save_failed")
    except Exception as ex:
        db.rollback()
        _log_exception("rooms_create Exception", ex)
        return _redirect("/rooms?err=save_failed")

    return _redirect("/rooms")

@app.post("/rooms/{room_id}/update")
def rooms_update(
    room_id: str,
    request: Request,
    db: Session = Depends(get_db),
    name: str = Form(...),
    floor: str = Form(""),
    status: str = Form(""),
):
    user = _require_user_and_project(request, db)

    r = db.get(Room, room_id)
    if not r or r.project_id != user.active_project_id:
        return _redirect("/rooms")

    room_name = _clean_str(name)
    if not room_name:
        return _redirect("/rooms?err=missing_name")

    # Enforce unique room name per project (case-insensitive), excluding this room
    dup = (
        db.query(Room.id)
        .filter(Room.project_id == user.active_project_id)
        .filter(func.lower(Room.name) == room_name.lower())
        .filter(Room.id != r.id)
        .first()
    )
    if dup:
        return _redirect("/rooms?err=duplicate")

    r.name = room_name
    r.floor = _clean_str(floor) or None
    r.status = _clean_str(status) or None
    _set_if_attr(r, "updated_at", utcnow())

    try:
        db.commit()
    except Exception as ex:
        db.rollback()
        _log_exception("rooms_update Exception", ex)
        return _redirect("/rooms?err=save_failed")

    return _redirect("/rooms")


@app.post("/rooms/{room_id}/delete")
def rooms_delete(room_id: str, request: Request, db: Session = Depends(get_db)):
    user = _require_user_and_project(request, db)

    r = db.get(Room, room_id)
    if not r or r.project_id != user.active_project_id:
        return _redirect("/rooms")

    # Safe: unlink tasks/expenses/documents that reference this room
    db.query(Task).filter(Task.project_id == user.active_project_id, Task.room_id == r.id).update(
        {"room_id": None}, synchronize_session=False
    )
    db.query(Expense).filter(Expense.project_id == user.active_project_id, Expense.room_id == r.id).update(
        {"room_id": None}, synchronize_session=False
    )
    db.query(Document).filter(Document.project_id == user.active_project_id, Document.room_id == r.id).update(
        {"room_id": None}, synchronize_session=False
    )

    db.delete(r)
    try:
        db.commit()
    except Exception as ex:
        db.rollback()
        _log_exception("rooms_delete Exception", ex)
        return _redirect("/rooms?err=delete_failed")

    return _redirect("/rooms")



# -----------------
# Expenses
# -----------------
@app.get("/expenses", response_class=HTMLResponse)
def expenses_list(request: Request, db: Session = Depends(get_db)):
    user = _require_user_and_project(request, db)

    active_project = db.get(Project, user.active_project_id)
    rooms = db.query(Room).filter(Room.project_id == active_project.id).order_by(Room.name.asc()).all()
    tasks = db.query(Task).filter(Task.project_id == active_project.id).order_by(Task.created_at.desc()).limit(200).all()

    room_id = request.query_params.get("room_id") or ""
    task_id = request.query_params.get("task_id") or ""

    q = db.query(Expense).filter(Expense.project_id == active_project.id)
    if room_id:
        q = q.filter(Expense.room_id == room_id)
    if task_id:
        q = q.filter(Expense.task_id == task_id)

    expenses = q.order_by(Expense.purchase_date.desc(), Expense.created_at.desc()).limit(200).all()

    # Documents linked to expenses (receipt counts + quick preview links)
    doc_rows = (
        db.query(Document.id, Document.expense_id, Document.title)
        .filter(Document.project_id == active_project.id)
        .filter(Document.expense_id.isnot(None))
        .order_by(Document.created_at.desc())
        .all()
    )

    docs_by_expense = {}
    for doc_id, exp_id, title in doc_rows:
        if not exp_id:
            continue
        docs_by_expense.setdefault(exp_id, []).append({"id": doc_id, "title": title or "Document"})



    return templates.TemplateResponse(
        "expenses.html",
        {
            "request": request,
            "user": user,
            "active_project": active_project,
            "rooms": rooms,
            "tasks": tasks,
            "expenses": expenses,
            "today": date.today().isoformat(),
            "room_id": room_id,
            "task_id": task_id,
            "err": request.query_params.get("err") or "",
            "docs_by_expense": docs_by_expense,  # NEW
        },
    )


@app.post("/expenses/create")
def expenses_create(
    request: Request,
    db: Session = Depends(get_db),
    purchase_date: str = Form(...),
    gross_amount: str = Form(...),
    description: str = Form(...),
    room_id: str = Form(""),
    task_id: str = Form(""),
    vat_rate: str = Form(""),
    vat_amount: str = Form(""),
    payment_method: str = Form(""),
    vendor: str = Form(""),
    notes: str = Form(""),
):
    user = _require_user_and_project(request, db)

    now = utcnow()
    d = date.fromisoformat(purchase_date)
    gross = Decimal(gross_amount)

    vr = Decimal(vat_rate) if _clean_str(vat_rate) else None
    va = Decimal(vat_amount) if _clean_str(vat_amount) else None
    net = (gross - va) if va is not None else None

    e = Expense(
        id=str(uuid4()),
        project_id=user.active_project_id,
        room_id=_clean_str(room_id) or None,
        task_id=_clean_str(task_id) or None,
        purchase_date=d,
        gross_amount=gross,
        description=_clean_str(description),
        notes=_clean_str(notes) or None,
        vat_rate=vr,
        vat_amount=va,
        net_amount=net,
        payment_method=_clean_str(payment_method) or None,
        vendor=_clean_str(vendor) or None,
    )
    _set_if_attr(e, "created_at", now)
    _set_if_attr(e, "updated_at", now)

    db.add(e)
    try:
        db.commit()
    except Exception as ex:
        db.rollback()
        _log_exception("expenses_create Exception", ex)
        return _redirect("/expenses?err=save_failed")

    return _redirect("/expenses")

@app.post("/expenses/{expense_id}/update")
def expenses_update(
    expense_id: str,
    request: Request,
    db: Session = Depends(get_db),
    purchase_date: str = Form(...),
    gross_amount: str = Form(...),
    description: str = Form(...),
    room_id: str = Form(""),
    task_id: str = Form(""),
    vat_rate: str = Form(""),
    vat_amount: str = Form(""),
    payment_method: str = Form(""),
    vendor: str = Form(""),
    notes: str = Form(""),
):
    user = _require_user_and_project(request, db)

    e = db.get(Expense, expense_id)
    if not e or e.project_id != user.active_project_id:
        return _redirect("/expenses")

    try:
        d = date.fromisoformat(purchase_date)
        gross = Decimal(gross_amount)

        vr = Decimal(vat_rate) if _clean_str(vat_rate) else None
        va = Decimal(vat_amount) if _clean_str(vat_amount) else None
        net = (gross - va) if va is not None else None

        e.purchase_date = d
        e.gross_amount = gross
        e.description = _clean_str(description)
        e.room_id = _clean_str(room_id) or None
        e.task_id = _clean_str(task_id) or None

        e.vat_rate = vr
        e.vat_amount = va
        e.net_amount = net

        e.payment_method = _clean_str(payment_method) or None
        _set_if_attr(e, "vendor", _clean_str(vendor) or None)
        e.notes = _clean_str(notes) or None

        _set_if_attr(e, "updated_at", utcnow())

        db.commit()
    except Exception as ex:
        db.rollback()
        _log_exception("expenses_update Exception", ex)
        return _redirect("/expenses?err=save_failed")

    return _redirect("/expenses")


@app.post("/expenses/{expense_id}/delete")
def expenses_delete(expense_id: str, request: Request, db: Session = Depends(get_db)):
    user = _require_user_and_project(request, db)

    e = db.get(Expense, expense_id)
    if not e or e.project_id != user.active_project_id:
        return _redirect("/expenses")

    try:
        # Unlink only documents in THIS project that reference this expense
        db.query(Document).filter(
            Document.project_id == user.active_project_id,
            Document.expense_id == e.id
        ).update(
            {"expense_id": None},
            synchronize_session=False,
        )

        db.delete(e)
        db.commit()
    except Exception as ex:
        db.rollback()
        _log_exception("expenses_delete Exception", ex)
        return _redirect("/expenses?err=delete_failed")

    return _redirect("/expenses")




# -----------------
# Tasks
# -----------------
@app.get("/tasks", response_class=HTMLResponse)
def tasks_board(request: Request, db: Session = Depends(get_db)):
    user = _require_user_and_project(request, db)

    active_project = db.get(Project, user.active_project_id)
    rooms = (
        db.query(Room)
        .filter(Room.project_id == active_project.id)
        .order_by(Room.name.asc())
        .all()
    )

    tasks = (
        db.query(Task)
        .filter(Task.project_id == active_project.id)
        .order_by(Task.created_at.desc())
        .all()
    )

    cols = {"todo": [], "doing": [], "blocked": [], "done": []}
    for t in tasks:
        cols.setdefault(t.status, cols["todo"]).append(t)

    return templates.TemplateResponse(
        "tasks.html",
        {
            "request": request,
            "user": user,
            "active_project": active_project,
            "rooms": rooms,
            "cols": cols,
            "err": request.query_params.get("err") or "",
            "page_layout": "wide",  # optional (only matters if your layout uses it)
        },
    )



@app.post("/tasks/create")
def tasks_create(
    request: Request,
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    room_id: str = Form(""),
    due_date: str = Form(""),
    priority: int = Form(3),
):
    user = _require_user_and_project(request, db)

    dd = date.fromisoformat(due_date) if _clean_str(due_date) else None
    now = utcnow()

    t = Task(
        id=str(uuid4()),
        project_id=user.active_project_id,
        room_id=_clean_str(room_id) or None,
        title=_clean_str(title),
        description=_clean_str(description) or None,
        due_date=dd,
        priority=int(priority),
        status="todo",
    )
    _set_if_attr(t, "created_at", now)
    _set_if_attr(t, "updated_at", now)

    db.add(t)
    try:
        db.commit()
    except Exception as ex:
        db.rollback()
        _log_exception("tasks_create Exception", ex)
        return _redirect("/tasks?err=save_failed")

    return _redirect("/tasks")


@app.post("/tasks/{task_id}/move")
def tasks_move(
    task_id: str,
    request: Request,
    db: Session = Depends(get_db),
    status: str = Form(...),
):
    user = _require_user_and_project(request, db)

    t = db.get(Task, task_id)
    if not t or t.project_id != user.active_project_id:
        return _redirect("/tasks")

    status = _clean_str(status).lower()
    if status not in ("todo", "doing", "blocked", "done"):
        return _redirect("/tasks")

    t.status = status
    if status == "done" and not t.completed_at:
        t.completed_at = utcnow()
    if status != "done":
        t.completed_at = None

    _set_if_attr(t, "updated_at", utcnow())

    db.commit()
    return _redirect("/tasks")

@app.post("/tasks/{task_id}/update")
def tasks_update(
    task_id: str,
    request: Request,
    db: Session = Depends(get_db),
    title: str = Form(...),
    description: str = Form(""),
    room_id: str = Form(""),
    due_date: str = Form(""),
    priority: int = Form(3),
    status: str = Form(""),  # optional, but handy for edits
):
    user = _require_user_and_project(request, db)

    t = db.get(Task, task_id)
    if not t or t.project_id != user.active_project_id:
        return _redirect("/tasks")

    title_c = _clean_str(title)
    if not title_c:
        return _redirect("/tasks?err=missing_title")

    dd = date.fromisoformat(due_date) if _clean_str(due_date) else None

    t.title = title_c
    t.description = _clean_str(description) or None
    t.room_id = _clean_str(room_id) or None
    t.due_date = dd
    t.priority = int(priority)

    st = _clean_str(status).lower()
    if st in ("todo", "doing", "blocked", "done"):
        t.status = st
        if st == "done" and not t.completed_at:
            t.completed_at = utcnow()
        if st != "done":
            t.completed_at = None

    _set_if_attr(t, "updated_at", utcnow())

    try:
        db.commit()
    except Exception as ex:
        db.rollback()
        _log_exception("tasks_update Exception", ex)
        return _redirect("/tasks?err=save_failed")

    return _redirect("/tasks")


@app.post("/tasks/{task_id}/delete")
def tasks_delete(task_id: str, request: Request, db: Session = Depends(get_db)):
    user = _require_user_and_project(request, db)

    t = db.get(Task, task_id)
    if not t or t.project_id != user.active_project_id:
        return _redirect("/tasks")

    # Unlink documents via join table
    db.query(DocumentTask).filter(DocumentTask.task_id == t.id).delete(synchronize_session=False)

    # Unlink expenses
    db.query(Expense).filter(Expense.project_id == user.active_project_id, Expense.task_id == t.id).update(
        {"task_id": None}, synchronize_session=False
    )

    db.delete(t)
    try:
        db.commit()
    except Exception as ex:
        db.rollback()
        _log_exception("tasks_delete Exception", ex)
        return _redirect("/tasks?err=delete_failed")

    return _redirect("/tasks")


# ----------------
# Documents (MinIO)
# ----------------
@app.get("/documents", response_class=HTMLResponse)
def documents(request: Request, db: Session = Depends(get_db)):
    user = _require_user_and_project(request, db)

    preselect_expense_id = _clean_str(request.query_params.get("expense_id") or "") or ""

    docs = (
        db.query(Document)
        .filter(Document.project_id == user.active_project_id)
        .order_by(Document.created_at.desc())
        .all()
    )

    rooms = db.query(Room).filter(Room.project_id == user.active_project_id).order_by(Room.name.asc()).all()
    tasks = db.query(Task).filter(Task.project_id == user.active_project_id).order_by(Task.created_at.desc()).limit(200).all()
    expenses = db.query(Expense).filter(Expense.project_id == user.active_project_id).order_by(Expense.created_at.desc()).limit(200).all()

    dt_rows = (
        db.query(DocumentTask)
        .join(Document, DocumentTask.document_id == Document.id)
        .filter(Document.project_id == user.active_project_id)
        .all()
    )
    doc_task_map = {}
    for row in dt_rows:
        doc_task_map.setdefault(row.document_id, []).append(row.task_id)

    return templates.TemplateResponse(
        "documents.html",
        {
            "request": request,
            "user": user,
            "title": "Documents",
            "docs": docs,
            "rooms": rooms,
            "tasks": tasks,
            "expenses": expenses,
            "doc_task_map": doc_task_map,
            "s3_enabled": s3_enabled(),
            "err": request.query_params.get("err") or "",
            "preselect_expense_id": preselect_expense_id,  # NEW
        },
    )


@app.post("/documents")
@app.post("/documents/upload")
async def documents_upload(
    request: Request,
    db: Session = Depends(get_db),
    file: UploadFile = File(...),
    doc_type: str = Form("receipt"),
    title: str = Form(""),
    notes: str = Form(""),
    tags: str = Form(""),
    room_id: str = Form(""),
    expense_id: str = Form(""),
    task_ids: list[str] | str | None = Form(None),
    photo_group: str = Form("before"),
):
    user = _require_user_and_project(request, db)

    room_id = _clean_str(room_id) or None
    expense_id = _clean_str(expense_id) or None
    task_ids_list = _parse_task_ids(task_ids)

    doc_type_n = _norm_doc_type(doc_type)
    photo_group_n = _norm_photo_group(doc_type_n, photo_group)
    tags_n = _norm_tags(tags)

    content = await file.read()
    safe_title = _clean_str(title) or (file.filename or "document")

    ym = utcnow().strftime("%Y-%m")
    original = (file.filename or "upload").replace(" ", "_")
    key = f"{user.active_project_id}/{ym}/{uuid4()}_{original}"

    upload_bytes(key=key, data=content, content_type=file.content_type)

    now = utcnow()
    d = Document(
        id=str(uuid4()),
        project_id=user.active_project_id,
        room_id=room_id,
        expense_id=expense_id,
        doc_type=doc_type_n,
        photo_group=photo_group_n,
        title=safe_title,
        original_filename=file.filename,
        content_type=file.content_type,
        size_bytes=len(content),
        s3_key=key,
        notes=_clean_str(notes) or None,
        tags=tags_n,
    )
    _set_if_attr(d, "created_at", now)
    _set_if_attr(d, "updated_at", now)

    db.add(d)
    db.commit()

    for tid in task_ids_list:
        t = db.get(Task, tid)
        if t and t.project_id == user.active_project_id:
            db.add(DocumentTask(document_id=d.id, task_id=tid, created_at=utcnow()))
    db.commit()

    return _redirect("/documents")


@app.post("/documents/{doc_id}/update")
def documents_update(
    doc_id: str,
    request: Request,
    db: Session = Depends(get_db),
    doc_type: str = Form("receipt"),
    photo_group: str = Form("before"),
    title: str = Form(""),
    notes: str = Form(""),
    tags: str = Form(""),
    room_id: str = Form(""),
    expense_id: str = Form(""),
    task_ids: list[str] | str | None = Form(None),
):
    user = _require_user_and_project(request, db)

    d = db.get(Document, doc_id)
    if not d or d.project_id != user.active_project_id:
        return _redirect("/documents")

    doc_type_n = _norm_doc_type(doc_type)
    photo_group_n = _norm_photo_group(doc_type_n, photo_group)

    d.doc_type = doc_type_n
    d.photo_group = photo_group_n
    d.title = _clean_str(title) or (d.original_filename or "document")
    d.notes = _clean_str(notes) or None
    d.tags = _norm_tags(tags)
    d.room_id = _clean_str(room_id) or None
    d.expense_id = _clean_str(expense_id) or None

    _set_if_attr(d, "updated_at", utcnow())

    new_task_ids = set(_parse_task_ids(task_ids))
    existing = db.query(DocumentTask).filter(DocumentTask.document_id == d.id).all()
    existing_ids = set(x.task_id for x in existing)

    to_remove = existing_ids - new_task_ids
    if to_remove:
        db.query(DocumentTask).filter(
            DocumentTask.document_id == d.id,
            DocumentTask.task_id.in_(list(to_remove))
        ).delete(synchronize_session=False)

    to_add = new_task_ids - existing_ids
    for tid in to_add:
        t = db.get(Task, tid)
        if t and t.project_id == user.active_project_id:
            db.add(DocumentTask(document_id=d.id, task_id=tid, created_at=utcnow()))

    db.commit()
    return _redirect("/documents")


@app.post("/documents/{doc_id}/delete")
def documents_delete(doc_id: str, request: Request, db: Session = Depends(get_db)):
    user = _require_user_and_project(request, db)

    d = db.get(Document, doc_id)
    if not d or d.project_id != user.active_project_id:
        return _redirect("/documents")

    db.delete(d)
    db.commit()
    return _redirect("/documents")


@app.get("/documents/{doc_id}/download")
def documents_download(doc_id: str, request: Request, db: Session = Depends(get_db)):
    user = _require_user_and_project(request, db)

    d = db.get(Document, doc_id)
    if not d or d.project_id != user.active_project_id:
        return _redirect("/documents")

    url = presigned_get_url(d.s3_key, expires_seconds=3600)
    return RedirectResponse(url=url, status_code=302)


@app.get("/documents/{doc_id}/preview")
def documents_preview(doc_id: str, request: Request, db: Session = Depends(get_db)):
    user = _require_user_and_project(request, db)

    d = db.get(Document, doc_id)
    if not d or d.project_id != user.active_project_id:
        return _redirect("/documents")

    url = presigned_get_url(d.s3_key, expires_seconds=3600)
    return RedirectResponse(url=url, status_code=302)
