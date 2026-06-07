import sqlite3
import os
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from typing import Optional

DB_PATH = os.path.join(os.path.dirname(__file__), "expenses.db")

SYSTEM_CATEGORIES = [
    "Food & Dining",
    "Transport",
    "Housing",
    "Entertainment",
    "Health",
    "Shopping",
    "Bills & Utilities",
    "Education",
    "Travel",
    "Other",
]

app = FastAPI()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                is_system INTEGER NOT NULL DEFAULT 0
            )"""
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                amount REAL NOT NULL,
                date TEXT,
                category_id INTEGER REFERENCES categories(id)
            )"""
        )
        # Migrate existing expenses table if needed
        for col_def in [("date", "TEXT"), ("category_id", "INTEGER")]:
            try:
                conn.execute(f"ALTER TABLE expenses ADD COLUMN {col_def[0]} {col_def[1]}")
            except Exception:
                pass
        # Seed immutable system categories
        for name in SYSTEM_CATEGORIES:
            conn.execute(
                "INSERT OR IGNORE INTO categories (name, is_system) VALUES (?, 1)",
                (name,),
            )
        conn.commit()


init_db()


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ── HTML pages ──────────────────────────────────────────────────────────────

def read_html(filename: str) -> str:
    path = os.path.join(os.path.dirname(__file__), filename)
    with open(path) as f:
        return f.read()


@app.get("/", response_class=HTMLResponse)
def index():
    return read_html("index.html")


@app.get("/categories-page", response_class=HTMLResponse)
def categories_page():
    return read_html("categories.html")


# ── Category endpoints ───────────────────────────────────────────────────────

@app.get("/categories")
def list_categories():
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM categories ORDER BY is_system DESC, name"
        ).fetchall()
        return [dict(r) for r in rows]


class CategoryIn(BaseModel):
    name: str


@app.post("/categories", status_code=201)
def add_category(cat: CategoryIn):
    name = cat.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM categories WHERE LOWER(name) = LOWER(?)", (name,)
        ).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Category already exists")
        cur = conn.execute(
            "INSERT INTO categories (name, is_system) VALUES (?, 0)", (name,)
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM categories WHERE id = ?", (cur.lastrowid,)
        ).fetchone()
        return dict(row)


@app.delete("/categories/{category_id}", status_code=204)
def delete_category(category_id: int):
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM categories WHERE id = ?", (category_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Category not found")
        if row["is_system"]:
            raise HTTPException(status_code=403, detail="Cannot delete a built-in category")
        conn.execute("DELETE FROM categories WHERE id = ?", (category_id,))
        conn.commit()


# ── Expense endpoints ────────────────────────────────────────────────────────

@app.get("/expenses")
def list_expenses():
    with get_db() as conn:
        rows = conn.execute(
            """SELECT e.id, e.name, e.amount, e.date, e.category_id,
                      c.name AS category_name
               FROM expenses e
               LEFT JOIN categories c ON e.category_id = c.id
               ORDER BY e.id DESC"""
        ).fetchall()
        return [dict(r) for r in rows]


class ExpenseIn(BaseModel):
    name: str
    amount: float
    date: Optional[str] = None
    category_id: Optional[int] = None


@app.post("/expenses", status_code=201)
def add_expense(expense: ExpenseIn):
    if not expense.name.strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if expense.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    with get_db() as conn:
        if expense.category_id is not None:
            cat = conn.execute(
                "SELECT id FROM categories WHERE id = ?", (expense.category_id,)
            ).fetchone()
            if not cat:
                raise HTTPException(status_code=400, detail="Invalid category")
        cur = conn.execute(
            "INSERT INTO expenses (name, amount, date, category_id) VALUES (?, ?, ?, ?)",
            (expense.name.strip(), expense.amount, expense.date, expense.category_id),
        )
        conn.commit()
        row = conn.execute(
            """SELECT e.id, e.name, e.amount, e.date, e.category_id,
                      c.name AS category_name
               FROM expenses e
               LEFT JOIN categories c ON e.category_id = c.id
               WHERE e.id = ?""",
            (cur.lastrowid,),
        ).fetchone()
        return dict(row)


@app.delete("/expenses/{expense_id}", status_code=204)
def delete_expense(expense_id: int):
    with get_db() as conn:
        result = conn.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
        conn.commit()
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="Expense not found")


@app.get("/expenses/total")
def get_total():
    with get_db() as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM expenses"
        ).fetchone()
        return {"total": row["total"]}
