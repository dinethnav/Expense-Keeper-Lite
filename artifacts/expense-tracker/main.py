import sqlite3
import os
from contextlib import contextmanager
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

DB_PATH = os.path.join(os.path.dirname(__file__), "expenses.db")

app = FastAPI()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS expenses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                amount REAL NOT NULL
            )"""
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


class ExpenseIn(BaseModel):
    name: str
    amount: float


@app.get("/", response_class=HTMLResponse)
def index():
    with open(os.path.join(os.path.dirname(__file__), "index.html")) as f:
        return f.read()


@app.get("/expenses")
def list_expenses():
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM expenses ORDER BY id DESC").fetchall()
        return [dict(r) for r in rows]


@app.post("/expenses", status_code=201)
def add_expense(expense: ExpenseIn):
    if not expense.name.strip():
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    if expense.amount <= 0:
        raise HTTPException(status_code=400, detail="Amount must be positive")
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO expenses (name, amount) VALUES (?, ?)",
            (expense.name.strip(), expense.amount),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM expenses WHERE id = ?", (cur.lastrowid,)).fetchone()
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
        row = conn.execute("SELECT COALESCE(SUM(amount), 0) as total FROM expenses").fetchone()
        return {"total": row["total"]}
