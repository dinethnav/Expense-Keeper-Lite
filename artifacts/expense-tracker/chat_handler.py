"""
AI chat handler with tool-calling for the expense tracker.
Supports: add expenses (single or bulk), analyze spending, set/get budgets,
          list categories, suggest budget allocations.
"""
import json
import os
import sqlite3
from datetime import date
from contextlib import contextmanager
from openai import OpenAI

DB_PATH = os.path.join(os.path.dirname(__file__), "expenses.db")

client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


@contextmanager
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# ── Tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add_expenses",
            "description": (
                "Add one or more expenses to the tracker. "
                "Use this whenever the user mentions spending money, buying something, "
                "or lists multiple purchases at once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expenses": {
                        "type": "array",
                        "description": "List of expenses to add.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string", "description": "Description of the expense"},
                                "amount": {"type": "number", "description": "Amount in dollars"},
                                "date": {"type": "string", "description": "Date as YYYY-MM-DD. Default to today if not specified."},
                                "category": {"type": "string", "description": "Category name (must match an existing category)"},
                            },
                            "required": ["name", "amount"],
                        },
                    }
                },
                "required": ["expenses"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_spending_summary",
            "description": "Get spending totals and breakdown by category for a given month.",
            "parameters": {
                "type": "object",
                "properties": {
                    "year_month": {
                        "type": "string",
                        "description": "Month in YYYY-MM format. Use current month if not specified.",
                    }
                },
                "required": ["year_month"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_expenses",
            "description": "Get the most recent expenses, optionally filtered by category or month.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "description": "Max number to return (default 10)"},
                    "category": {"type": "string", "description": "Filter by category name"},
                    "year_month": {"type": "string", "description": "Filter by month YYYY-MM"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_categories",
            "description": "Get all available expense categories.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_budget",
            "description": "Set or update the total monthly budget for a given month.",
            "parameters": {
                "type": "object",
                "properties": {
                    "year_month": {"type": "string", "description": "Month in YYYY-MM format"},
                    "total_amount": {"type": "number", "description": "Total budget amount in dollars"},
                },
                "required": ["year_month", "total_amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_category_budget",
            "description": "Allocate a budget amount to a specific category for a month.",
            "parameters": {
                "type": "object",
                "properties": {
                    "year_month": {"type": "string", "description": "Month in YYYY-MM format"},
                    "category": {"type": "string", "description": "Category name"},
                    "amount": {"type": "number", "description": "Budget amount for this category"},
                },
                "required": ["year_month", "category", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_budget",
            "description": "Get the budget plan for a given month, including category allocations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "year_month": {"type": "string", "description": "Month in YYYY-MM format"},
                },
                "required": ["year_month"],
            },
        },
    },
]


# ── Tool implementations ───────────────────────────────────────────────────────

def _run_add_expenses(args: dict) -> dict:
    today = date.today().isoformat()
    added = []
    errors = []
    with get_db() as conn:
        categories = {
            r["name"].lower(): r["id"]
            for r in conn.execute("SELECT id, name FROM categories").fetchall()
        }
        for exp in args.get("expenses", []):
            name = exp.get("name", "").strip()
            amount = exp.get("amount")
            exp_date = exp.get("date") or today
            cat_name = (exp.get("category") or "").strip()

            category_id = None
            if cat_name:
                category_id = categories.get(cat_name.lower())
                if category_id is None:
                    # Try partial match
                    for key, cid in categories.items():
                        if cat_name.lower() in key:
                            category_id = cid
                            break

            if not name or not amount or amount <= 0:
                errors.append(f"Skipped invalid expense: {exp}")
                continue

            cur = conn.execute(
                "INSERT INTO expenses (name, amount, date, category_id) VALUES (?,?,?,?)",
                (name, amount, exp_date, category_id),
            )
            row = conn.execute("""
                SELECT e.id, e.name, e.amount, e.date, c.name AS category_name
                FROM expenses e LEFT JOIN categories c ON e.category_id=c.id WHERE e.id=?
            """, (cur.lastrowid,)).fetchone()
            added.append(dict(row))
        conn.commit()
    result = {"added": added, "count": len(added)}
    if errors:
        result["errors"] = errors
    return result


def _run_get_spending_summary(args: dict) -> dict:
    ym = args["year_month"]
    with get_db() as conn:
        total = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS t FROM expenses WHERE date LIKE ?",
            (f"{ym}%",)
        ).fetchone()["t"]
        cats = conn.execute("""
            SELECT c.name AS category, COALESCE(SUM(e.amount),0) AS spent
            FROM expenses e JOIN categories c ON e.category_id=c.id
            WHERE e.date LIKE ? GROUP BY c.id ORDER BY spent DESC
        """, (f"{ym}%",)).fetchall()
        no_cat = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS t FROM expenses WHERE date LIKE ? AND category_id IS NULL",
            (f"{ym}%",)
        ).fetchone()["t"]
    breakdown = [{"category": r["category"], "spent": r["spent"]} for r in cats]
    if no_cat > 0:
        breakdown.append({"category": "Uncategorized", "spent": no_cat})
    return {"year_month": ym, "total": total, "by_category": breakdown}


def _run_get_recent_expenses(args: dict) -> dict:
    limit = args.get("limit", 10)
    cat_filter = args.get("category", "")
    ym = args.get("year_month", "")
    with get_db() as conn:
        conditions = []
        params = []
        if cat_filter:
            conditions.append("LOWER(c.name) LIKE ?")
            params.append(f"%{cat_filter.lower()}%")
        if ym:
            conditions.append("e.date LIKE ?")
            params.append(f"{ym}%")
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        rows = conn.execute(f"""
            SELECT e.id, e.name, e.amount, e.date, c.name AS category_name
            FROM expenses e LEFT JOIN categories c ON e.category_id=c.id
            {where} ORDER BY e.id DESC LIMIT ?
        """, params + [limit]).fetchall()
    return {"expenses": [dict(r) for r in rows], "count": len(rows)}


def _run_list_categories(_: dict) -> dict:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, is_system FROM categories ORDER BY is_system DESC, name"
        ).fetchall()
    return {"categories": [dict(r) for r in rows]}


def _run_set_budget(args: dict) -> dict:
    ym = args["year_month"]
    total = args["total_amount"]
    with get_db() as conn:
        existing = conn.execute("SELECT id FROM budgets WHERE year_month=?", (ym,)).fetchone()
        if existing:
            conn.execute("UPDATE budgets SET total_amount=? WHERE year_month=?", (total, ym))
        else:
            conn.execute("INSERT INTO budgets (year_month, total_amount) VALUES (?,?)", (ym, total))
        conn.commit()
    return {"ok": True, "year_month": ym, "total_amount": total}


def _run_set_category_budget(args: dict) -> dict:
    ym = args["year_month"]
    cat_name = args["category"].strip()
    amount = args["amount"]
    with get_db() as conn:
        # Find category
        row = conn.execute(
            "SELECT id FROM categories WHERE LOWER(name)=LOWER(?)", (cat_name,)
        ).fetchone()
        if not row:
            # partial match
            row = conn.execute(
                "SELECT id, name FROM categories WHERE LOWER(name) LIKE ?", (f"%{cat_name.lower()}%",)
            ).fetchone()
        if not row:
            return {"error": f"Category '{cat_name}' not found"}
        category_id = row["id"]
        budget = conn.execute("SELECT id FROM budgets WHERE year_month=?", (ym,)).fetchone()
        if not budget:
            return {"error": f"No budget exists for {ym}. Create a total budget first."}
        conn.execute("""
            INSERT INTO budget_categories (budget_id, category_id, amount)
            VALUES (?,?,?)
            ON CONFLICT(budget_id, category_id) DO UPDATE SET amount=excluded.amount
        """, (budget["id"], category_id, amount))
        conn.commit()
    return {"ok": True, "year_month": ym, "category": cat_name, "amount": amount}


def _run_get_budget(args: dict) -> dict:
    ym = args["year_month"]
    with get_db() as conn:
        budget = conn.execute("SELECT * FROM budgets WHERE year_month=?", (ym,)).fetchone()
        if not budget:
            return {"year_month": ym, "exists": False}
        budget = dict(budget)
        cats = conn.execute("""
            SELECT c.name AS category, bc.amount
            FROM budget_categories bc JOIN categories c ON bc.category_id=c.id
            WHERE bc.budget_id=? ORDER BY bc.amount DESC
        """, (budget["id"],)).fetchall()
        budget["categories"] = [dict(r) for r in cats]
        budget["exists"] = True
    return budget


TOOL_FNS = {
    "add_expenses": _run_add_expenses,
    "get_spending_summary": _run_get_spending_summary,
    "get_recent_expenses": _run_get_recent_expenses,
    "list_categories": _run_list_categories,
    "set_budget": _run_set_budget,
    "set_category_budget": _run_set_category_budget,
    "get_budget": _run_get_budget,
}


# ── Main streaming chat function ──────────────────────────────────────────────

SYSTEM_PROMPT = f"""You are a smart, friendly personal finance assistant built into an expense tracker app.
Today is {date.today().isoformat()}.

You can:
- Add one OR multiple expenses at once (always use add_expenses tool — never pretend to add without using it)
- Analyze spending patterns and give insights
- Help set up monthly budgets and category allocations
- Answer questions about past expenses

Guidelines:
- Be concise and direct. Use bullet points or short lists when adding multiple items.
- When the user mentions buying things or spending money, immediately use add_expenses.
- When adding expenses, infer the category from context if not stated.
- When the user asks "how am I doing this month?" or similar, call get_spending_summary first.
- Format dollar amounts as $X.XX.
- If a tool call modifies data (adds expenses, sets budget), briefly confirm what was done.
- Never make up expense data — always use tools to read/write real data.
- If the user wants to set up a budget, ask for a total amount first, then offer to allocate by category.
"""


def stream_chat(messages: list) -> "generator":
    """
    Yields SSE-formatted strings. Each is either:
      data: {"type": "text", "content": "..."}
      data: {"type": "tool_result", "name": "...", "result": {...}}
      data: {"type": "done"}
    """
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    while True:
        response = client.chat.completions.create(
            model="gpt-4.1-nano",
            messages=full_messages,
            tools=TOOLS,
            tool_choice="auto",
            stream=True,
            max_completion_tokens=1024,
        )

        # Collect streamed response
        tool_calls_raw = {}
        text_buffer = ""
        finish_reason = None
        assistant_msg = {"role": "assistant", "content": None, "tool_calls": []}

        for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta is None:
                continue
            finish_reason = chunk.choices[0].finish_reason or finish_reason

            # Text content
            if delta.content:
                text_buffer += delta.content
                yield f"data: {json.dumps({'type': 'text', 'content': delta.content})}\n\n"

            # Tool calls
            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in tool_calls_raw:
                        tool_calls_raw[idx] = {"id": "", "name": "", "args": ""}
                    if tc.id:
                        tool_calls_raw[idx]["id"] += tc.id
                    if tc.function and tc.function.name:
                        tool_calls_raw[idx]["name"] += tc.function.name
                    if tc.function and tc.function.arguments:
                        tool_calls_raw[idx]["args"] += tc.function.arguments

        if text_buffer:
            assistant_msg["content"] = text_buffer
        assistant_msg["tool_calls"] = [
            {
                "id": v["id"],
                "type": "function",
                "function": {"name": v["name"], "arguments": v["args"]},
            }
            for v in tool_calls_raw.values()
        ] or None
        if not assistant_msg["tool_calls"]:
            del assistant_msg["tool_calls"]

        full_messages.append(assistant_msg)

        if not tool_calls_raw or finish_reason == "stop":
            break

        # Execute tools and feed results back
        for v in tool_calls_raw.values():
            fn = TOOL_FNS.get(v["name"])
            if fn:
                try:
                    args = json.loads(v["args"])
                    result = fn(args)
                except Exception as e:
                    result = {"error": str(e)}
            else:
                result = {"error": f"Unknown tool: {v['name']}"}

            yield f"data: {json.dumps({'type': 'tool_result', 'name': v['name'], 'result': result})}\n\n"

            full_messages.append({
                "role": "tool",
                "tool_call_id": v["id"],
                "content": json.dumps(result),
            })

    yield f"data: {json.dumps({'type': 'done'})}\n\n"
