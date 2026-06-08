import sqlite3
import os
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse, RedirectResponse, JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from pydantic import BaseModel
from typing import Optional, List
from auth import (
    hash_password, verify_password, get_session_user,
    require_user, require_user_id, require_admin,
    google_auth_url, google_exchange_code, GOOGLE_ENABLED,
)

DB_PATH = os.path.join(os.path.dirname(__file__), "expenses.db")

SYSTEM_CATEGORIES = [
    "Food & Dining", "Transport", "Housing", "Entertainment",
    "Health", "Shopping", "Bills & Utilities", "Education", "Travel", "Other",
]

app = FastAPI()
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("SESSION_SECRET", "change-me-in-production"),
    max_age=86400 * 30,
    https_only=False,
)


# ── DB setup ──────────────────────────────────────────────────────────────────

def _migrate(conn):
    """Idempotent migrations — safe to call on every start."""
    # Users table
    conn.execute("""CREATE TABLE IF NOT EXISTS users (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        email      TEXT NOT NULL UNIQUE,
        name       TEXT,
        google_id  TEXT UNIQUE,
        password_hash TEXT,
        is_admin   INTEGER NOT NULL DEFAULT 0,
        is_active  INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""")

    # Add user_id to expenses
    existing = {r[1] for r in conn.execute("PRAGMA table_info(expenses)").fetchall()}
    if "user_id" not in existing:
        conn.execute("ALTER TABLE expenses ADD COLUMN user_id INTEGER REFERENCES users(id)")

    # Add user_id to categories
    existing = {r[1] for r in conn.execute("PRAGMA table_info(categories)").fetchall()}
    if "user_id" not in existing:
        conn.execute("ALTER TABLE categories ADD COLUMN user_id INTEGER REFERENCES users(id)")

    # Recreate budgets with (user_id, year_month) unique constraint
    existing = {r[1] for r in conn.execute("PRAGMA table_info(budgets)").fetchall()}
    if "user_id" not in existing:
        conn.execute("""CREATE TABLE budgets_v2 (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER REFERENCES users(id),
            year_month   TEXT NOT NULL,
            total_amount REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, year_month)
        )""")
        conn.execute("INSERT INTO budgets_v2 SELECT id, NULL, year_month, total_amount FROM budgets")
        conn.execute("DROP TABLE budgets")
        conn.execute("ALTER TABLE budgets_v2 RENAME TO budgets")

    conn.commit()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS categories (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT NOT NULL UNIQUE,
            is_system INTEGER NOT NULL DEFAULT 0,
            user_id   INTEGER
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS expenses (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            amount      REAL NOT NULL,
            date        TEXT,
            category_id INTEGER,
            user_id     INTEGER
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS budgets (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER,
            year_month   TEXT NOT NULL,
            total_amount REAL NOT NULL DEFAULT 0,
            UNIQUE(user_id, year_month)
        )""")
        conn.execute("""CREATE TABLE IF NOT EXISTS budget_categories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            budget_id   INTEGER NOT NULL REFERENCES budgets(id) ON DELETE CASCADE,
            category_id INTEGER NOT NULL REFERENCES categories(id),
            amount      REAL NOT NULL,
            UNIQUE(budget_id, category_id)
        )""")
        # Existing DB columns (migration from old schema)
        _migrate(conn)
        for name in SYSTEM_CATEGORIES:
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, is_system) VALUES (?, 1)", (name,)
            )
        conn.commit()


init_db()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def read_html(filename: str) -> str:
    path = os.path.join(os.path.dirname(__file__), filename)
    with open(path) as f:
        return f.read()


def assign_legacy_data(conn, user_id: int):
    """Option A: assign orphaned (pre-auth) data to the first-registered user."""
    conn.execute("UPDATE expenses SET user_id=? WHERE user_id IS NULL", (user_id,))
    conn.execute(
        "UPDATE categories SET user_id=? WHERE user_id IS NULL AND is_system=0", (user_id,)
    )
    conn.execute("UPDATE budgets SET user_id=? WHERE user_id IS NULL", (user_id,))
    conn.commit()


# ── Pages ────────────────────────────────────────────────────────────────────

@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    if get_session_user(request):
        return RedirectResponse("/", status_code=302)
    return read_html("login.html")

@app.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request):
    if get_session_user(request):
        return RedirectResponse("/", status_code=302)
    return read_html("signup.html")

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    if not get_session_user(request):
        return RedirectResponse("/login", status_code=302)
    return read_html("index.html")

@app.get("/categories-page", response_class=HTMLResponse)
def categories_page(request: Request):
    if not get_session_user(request):
        return RedirectResponse("/login", status_code=302)
    return read_html("categories.html")

@app.get("/budget-page", response_class=HTMLResponse)
def budget_page(request: Request):
    if not get_session_user(request):
        return RedirectResponse("/login", status_code=302)
    return read_html("budget.html")

@app.get("/admin", response_class=HTMLResponse)
def admin_page(request: Request):
    user = get_session_user(request)
    if not user:
        return RedirectResponse("/login", status_code=302)
    if not user.get("is_admin"):
        return RedirectResponse("/", status_code=302)
    return read_html("admin.html")


# ── Auth routes ───────────────────────────────────────────────────────────────

class LoginBody(BaseModel):
    email: str
    password: str

class SignupBody(BaseModel):
    email: str
    password: str
    name: Optional[str] = None

@app.post("/auth/login")
def auth_login(body: LoginBody, request: Request):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email=?", (body.email.strip().lower(),)
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid email or password")
    user = dict(row)
    if not user.get("password_hash"):
        raise HTTPException(status_code=401, detail="Use Google to sign in for this account")
    if not verify_password(body.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if not user["is_active"]:
        raise HTTPException(status_code=403, detail="Account disabled")
    request.session["user_id"] = user["id"]
    return {"ok": True}

@app.post("/auth/signup")
def auth_signup(body: SignupBody, request: Request):
    email = body.email.strip().lower()
    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    with get_db() as conn:
        if conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone():
            raise HTTPException(status_code=409, detail="An account with this email already exists")
        user_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        is_first = user_count == 0
        cur = conn.execute(
            "INSERT INTO users (email, name, password_hash, is_admin, is_active) VALUES (?,?,?,?,1)",
            (email, (body.name or "").strip() or None, hash_password(body.password), 1 if is_first else 0),
        )
        user_id = cur.lastrowid
        conn.commit()
        if is_first:
            assign_legacy_data(conn, user_id)
    request.session["user_id"] = user_id
    return {"ok": True}

@app.get("/auth/logout")
def auth_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)

@app.get("/auth/google-enabled")
def google_enabled():
    return {"enabled": GOOGLE_ENABLED}

@app.get("/auth/google")
def google_login(request: Request):
    if not GOOGLE_ENABLED:
        return RedirectResponse("/login?error=google_disabled", status_code=302)
    return RedirectResponse(google_auth_url(request), status_code=302)

@app.get("/auth/google/callback")
async def google_callback(request: Request, code: str = None, error: str = None):
    if error or not code:
        return RedirectResponse("/login?error=google_error", status_code=302)
    try:
        info = await google_exchange_code(code, request)
    except Exception:
        return RedirectResponse("/login?error=google_error", status_code=302)

    google_id = info.get("id")
    email     = (info.get("email") or "").strip().lower()
    name      = info.get("name")

    with get_db() as conn:
        # Try to find by google_id first, then by email
        row = conn.execute("SELECT * FROM users WHERE google_id=?", (google_id,)).fetchone()
        if not row and email:
            row = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
            if row:
                conn.execute("UPDATE users SET google_id=? WHERE id=?", (google_id, row["id"]))
                conn.commit()
        if not row:
            user_count = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
            is_first = user_count == 0
            cur = conn.execute(
                "INSERT INTO users (email, name, google_id, is_admin, is_active) VALUES (?,?,?,?,1)",
                (email, name, google_id, 1 if is_first else 0),
            )
            user_id = cur.lastrowid
            conn.commit()
            if is_first:
                assign_legacy_data(conn, user_id)
            row = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

        user = dict(row)

    if not user["is_active"]:
        return RedirectResponse("/login?error=disabled", status_code=302)

    request.session["user_id"] = user["id"]
    return RedirectResponse("/", status_code=302)


# ── Current user ──────────────────────────────────────────────────────────────

@app.get("/me")
def get_me(request: Request):
    user = require_user(request)
    return {k: user[k] for k in ("id", "name", "email", "is_admin")}


# ── Categories ────────────────────────────────────────────────────────────────

@app.get("/categories")
def list_categories(request: Request):
    user = require_user(request)
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM categories WHERE is_system=1 OR user_id=? ORDER BY is_system DESC, name",
            (user["id"],)
        ).fetchall()
        return [dict(r) for r in rows]

class CategoryIn(BaseModel):
    name: str

@app.post("/categories", status_code=201)
def add_category(cat: CategoryIn, request: Request):
    user = require_user(request)
    name = cat.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    with get_db() as conn:
        if conn.execute(
            "SELECT id FROM categories WHERE LOWER(name)=LOWER(?) AND (is_system=1 OR user_id=?)",
            (name, user["id"])
        ).fetchone():
            raise HTTPException(status_code=409, detail="Category already exists")
        cur = conn.execute(
            "INSERT INTO categories (name, is_system, user_id) VALUES (?, 0, ?)", (name, user["id"])
        )
        conn.commit()
        return dict(conn.execute("SELECT * FROM categories WHERE id=?", (cur.lastrowid,)).fetchone())

@app.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: int, request: Request):
    user = require_user(request)
    with get_db() as conn:
        row = conn.execute("SELECT * FROM categories WHERE id=?", (category_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        if row["is_system"]:
            raise HTTPException(status_code=403, detail="Cannot delete a built-in category")
        if row["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Not your category")
        conn.execute("DELETE FROM categories WHERE id=?", (category_id,))
        conn.commit()


# ── Expenses ──────────────────────────────────────────────────────────────────

@app.get("/expenses")
def list_expenses(request: Request):
    user = require_user(request)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT e.id, e.name, e.amount, e.date, e.category_id, c.name AS category_name
            FROM expenses e LEFT JOIN categories c ON e.category_id=c.id
            WHERE e.user_id=?
            ORDER BY e.id DESC
        """, (user["id"],)).fetchall()
        return [dict(r) for r in rows]

class ExpenseIn(BaseModel):
    name: str
    amount: float
    date: Optional[str] = None
    category_id: Optional[int] = None

@app.post("/expenses", status_code=201)
def add_expense(expense: ExpenseIn, request: Request):
    user = require_user(request)
    if not expense.name.strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if expense.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    with get_db() as conn:
        if expense.category_id is not None:
            if not conn.execute("SELECT id FROM categories WHERE id=?", (expense.category_id,)).fetchone():
                raise HTTPException(status_code=400, detail="Invalid category")
        cur = conn.execute(
            "INSERT INTO expenses (name, amount, date, category_id, user_id) VALUES (?,?,?,?,?)",
            (expense.name.strip(), expense.amount, expense.date, expense.category_id, user["id"]),
        )
        conn.commit()
        row = conn.execute("""
            SELECT e.id, e.name, e.amount, e.date, e.category_id, c.name AS category_name
            FROM expenses e LEFT JOIN categories c ON e.category_id=c.id WHERE e.id=?
        """, (cur.lastrowid,)).fetchone()
        return dict(row)

@app.delete("/expenses/{expense_id}", status_code=204)
def delete_expense(expense_id: int, request: Request):
    user = require_user(request)
    with get_db() as conn:
        row = conn.execute("SELECT user_id FROM expenses WHERE id=?", (expense_id,)).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Not found")
        if row["user_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Not your expense")
        conn.execute("DELETE FROM expenses WHERE id=?", (expense_id,))
        conn.commit()

@app.get("/expenses/total")
def get_total(request: Request):
    user = require_user(request)
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE user_id=?", (user["id"],)
        ).fetchone()
        return {"total": row["total"]}

@app.get("/expenses/month/{year_month}")
def get_month_summary(year_month: str, request: Request):
    user = require_user(request)
    with get_db() as conn:
        total_row = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS total FROM expenses WHERE user_id=? AND date LIKE ?",
            (user["id"], f"{year_month}%")
        ).fetchone()
        cat_rows = conn.execute("""
            SELECT c.id AS category_id, c.name AS category_name,
                   COALESCE(SUM(e.amount),0) AS spent
            FROM expenses e
            JOIN categories c ON e.category_id=c.id
            WHERE e.user_id=? AND e.date LIKE ?
            GROUP BY c.id
        """, (user["id"], f"{year_month}%")).fetchall()
        return {
            "year_month": year_month,
            "total": total_row["total"],
            "by_category": [dict(r) for r in cat_rows],
        }


# ── Budgets ───────────────────────────────────────────────────────────────────

@app.get("/budgets/{year_month}")
def get_budget(year_month: str, request: Request):
    user = require_user(request)
    with get_db() as conn:
        budget = conn.execute(
            "SELECT * FROM budgets WHERE user_id=? AND year_month=?", (user["id"], year_month)
        ).fetchone()
        if not budget:
            return None
        budget = dict(budget)
        cat_rows = conn.execute("""
            SELECT bc.id, bc.category_id, bc.amount, c.name AS category_name
            FROM budget_categories bc
            JOIN categories c ON bc.category_id=c.id
            WHERE bc.budget_id=?
            ORDER BY c.name
        """, (budget["id"],)).fetchall()
        budget["categories"] = [dict(r) for r in cat_rows]
        return budget

class BudgetIn(BaseModel):
    total_amount: float

@app.put("/budgets/{year_month}", status_code=200)
def upsert_budget(year_month: str, body: BudgetIn, request: Request):
    user = require_user(request)
    if body.total_amount < 0:
        raise HTTPException(status_code=400, detail="Amount must be non-negative")
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM budgets WHERE user_id=? AND year_month=?", (user["id"], year_month)
        ).fetchone()
        if existing:
            conn.execute(
                "UPDATE budgets SET total_amount=? WHERE user_id=? AND year_month=?",
                (body.total_amount, user["id"], year_month)
            )
        else:
            conn.execute(
                "INSERT INTO budgets (user_id, year_month, total_amount) VALUES (?,?,?)",
                (user["id"], year_month, body.total_amount)
            )
        conn.commit()
        return get_budget(year_month, request)

@app.delete("/budgets/{year_month}", status_code=204)
def delete_budget(year_month: str, request: Request):
    user = require_user(request)
    with get_db() as conn:
        result = conn.execute(
            "DELETE FROM budgets WHERE user_id=? AND year_month=?", (user["id"], year_month)
        )
        conn.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Not found")

class BudgetCategoryIn(BaseModel):
    amount: float

@app.put("/budgets/{year_month}/categories/{category_id}", status_code=200)
def upsert_budget_category(year_month: str, category_id: int, body: BudgetCategoryIn, request: Request):
    user = require_user(request)
    if body.amount < 0:
        raise HTTPException(status_code=400, detail="Amount must be non-negative")
    with get_db() as conn:
        budget = conn.execute(
            "SELECT id FROM budgets WHERE user_id=? AND year_month=?", (user["id"], year_month)
        ).fetchone()
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found — create it first")
        conn.execute("""
            INSERT INTO budget_categories (budget_id, category_id, amount)
            VALUES (?,?,?)
            ON CONFLICT(budget_id, category_id) DO UPDATE SET amount=excluded.amount
        """, (budget["id"], category_id, body.amount))
        conn.commit()
        return {"ok": True}

@app.delete("/budgets/{year_month}/categories/{category_id}", status_code=204)
def delete_budget_category(year_month: str, category_id: int, request: Request):
    user = require_user(request)
    with get_db() as conn:
        budget = conn.execute(
            "SELECT id FROM budgets WHERE user_id=? AND year_month=?", (user["id"], year_month)
        ).fetchone()
        if not budget:
            raise HTTPException(status_code=404, detail="Budget not found")
        conn.execute(
            "DELETE FROM budget_categories WHERE budget_id=? AND category_id=?",
            (budget["id"], category_id)
        )
        conn.commit()


# ── Admin API ─────────────────────────────────────────────────────────────────

@app.get("/admin/stats")
def admin_stats(request: Request):
    require_admin(request)
    with get_db() as conn:
        total_users = conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]
        new_month   = conn.execute(
            "SELECT COUNT(*) AS c FROM users WHERE created_at >= date('now','start of month')"
        ).fetchone()["c"]
        active      = conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_active=1").fetchone()["c"]
        total_exp   = conn.execute("SELECT COUNT(*) AS c FROM expenses").fetchone()["c"]
    return {
        "total_users": total_users,
        "new_this_month": new_month,
        "active_users": active,
        "total_expenses": total_exp,
    }

@app.get("/admin/users")
def admin_list_users(request: Request):
    require_admin(request)
    with get_db() as conn:
        rows = conn.execute("""
            SELECT u.id, u.email, u.name, u.is_admin, u.is_active, u.created_at,
                   COUNT(DISTINCT e.id) AS expense_count,
                   COUNT(DISTINCT b.id) AS budget_count
            FROM users u
            LEFT JOIN expenses e ON e.user_id=u.id
            LEFT JOIN budgets  b ON b.user_id=u.id
            GROUP BY u.id
            ORDER BY u.created_at DESC
        """).fetchall()
        return [dict(r) for r in rows]

class AdminUserUpdate(BaseModel):
    is_admin:  Optional[int] = None
    is_active: Optional[int] = None

@app.put("/admin/users/{user_id}")
def admin_update_user(user_id: int, body: AdminUserUpdate, request: Request):
    me = require_admin(request)
    if user_id == me["id"]:
        raise HTTPException(status_code=400, detail="Cannot modify your own account")
    with get_db() as conn:
        if not conn.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone():
            raise HTTPException(status_code=404, detail="User not found")
        if body.is_admin is not None:
            conn.execute("UPDATE users SET is_admin=? WHERE id=?", (body.is_admin, user_id))
        if body.is_active is not None:
            conn.execute("UPDATE users SET is_active=? WHERE id=?", (body.is_active, user_id))
        conn.commit()
        return {"ok": True}


# ── Chat ──────────────────────────────────────────────────────────────────────

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    messages: List[ChatMessage]

@app.get("/chat.js")
def serve_chat_js():
    path = os.path.join(os.path.dirname(__file__), "chat.js")
    return FileResponse(path, media_type="application/javascript")

@app.post("/chat")
def chat_endpoint(req: ChatRequest, request: Request):
    from chat_handler import stream_chat
    user = require_user(request)
    msgs = [{"role": m.role, "content": m.content} for m in req.messages]
    return StreamingResponse(
        stream_chat(msgs, user_id=user["id"]),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
