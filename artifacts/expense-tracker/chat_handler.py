"""
AI chat handler with tool-calling for the expense tracker.
All tool implementations filter by user_id for per-user data isolation.
"""
import json
import os
import sqlite3
from datetime import date
from contextlib import contextmanager
from openai import OpenAI

DB_PATH = os.path.join(os.path.dirname(__file__), "expenses.db")
client  = OpenAI(api_key=os.environ["OPENAI_API_KEY"])


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
                "Add one or more expenses. Use this whenever the user mentions spending money, "
                "buying something, or lists multiple purchases at once."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "expenses": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name":     {"type": "string"},
                                "amount":   {"type": "number"},
                                "date":     {"type": "string", "description": "YYYY-MM-DD, default today"},
                                "category": {"type": "string", "description": "Existing category name"},
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
                    "year_month": {"type": "string", "description": "YYYY-MM. Current month if not specified."},
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
                    "limit":      {"type": "integer", "description": "Max to return (default 10)"},
                    "category":   {"type": "string"},
                    "year_month": {"type": "string", "description": "Filter by YYYY-MM"},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_categories",
            "description": "Get all available expense categories for the current user.",
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
                    "year_month":    {"type": "string"},
                    "total_amount":  {"type": "number"},
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
                    "year_month": {"type": "string"},
                    "category":   {"type": "string"},
                    "amount":     {"type": "number"},
                },
                "required": ["year_month", "category", "amount"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_budget",
            "description": (
                "Get the planned budget for a month, including total budget, per-category allocations, "
                "and actual spending vs. plan for each category. Use this whenever the user asks about "
                "their budget plan, what they planned to spend, or budget status for any month."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "year_month": {"type": "string", "description": "YYYY-MM. Current month if not specified."},
                },
                "required": ["year_month"],
            },
        },
    },
]


# ── Tool implementations (all scoped to user_id) ──────────────────────────────

def _run_add_expenses(args: dict, user_id: int) -> dict:
    today = date.today().isoformat()
    added, errors = [], []
    with get_db() as conn:
        categories = {
            r["name"].lower(): r["id"]
            for r in conn.execute(
                "SELECT id, name FROM categories WHERE is_system=1 OR user_id=?", (user_id,)
            ).fetchall()
        }
        for exp in args.get("expenses", []):
            name     = exp.get("name", "").strip()
            amount   = exp.get("amount")
            exp_date = exp.get("date") or today
            cat_name = (exp.get("category") or "").strip()

            category_id = None
            if cat_name:
                category_id = categories.get(cat_name.lower())
                if category_id is None:
                    for key, cid in categories.items():
                        if cat_name.lower() in key:
                            category_id = cid
                            break

            if not name or not amount or amount <= 0:
                errors.append(f"Skipped invalid: {exp}")
                continue

            cur = conn.execute(
                "INSERT INTO expenses (name, amount, date, category_id, user_id) VALUES (?,?,?,?,?)",
                (name, amount, exp_date, category_id, user_id),
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


def _run_get_spending_summary(args: dict, user_id: int) -> dict:
    ym = args["year_month"]
    with get_db() as conn:
        total = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS t FROM expenses WHERE user_id=? AND date LIKE ?",
            (user_id, f"{ym}%")
        ).fetchone()["t"]
        cats = conn.execute("""
            SELECT c.name AS category, COALESCE(SUM(e.amount),0) AS spent
            FROM expenses e JOIN categories c ON e.category_id=c.id
            WHERE e.user_id=? AND e.date LIKE ?
            GROUP BY c.id ORDER BY spent DESC
        """, (user_id, f"{ym}%")).fetchall()
        no_cat = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS t FROM expenses WHERE user_id=? AND date LIKE ? AND category_id IS NULL",
            (user_id, f"{ym}%")
        ).fetchone()["t"]
    breakdown = [{"category": r["category"], "spent": r["spent"]} for r in cats]
    if no_cat > 0:
        breakdown.append({"category": "Uncategorized", "spent": no_cat})
    return {"year_month": ym, "total": total, "by_category": breakdown}


def _run_get_recent_expenses(args: dict, user_id: int) -> dict:
    limit      = args.get("limit", 10)
    cat_filter = args.get("category", "")
    ym         = args.get("year_month", "")
    with get_db() as conn:
        conditions, params = ["e.user_id=?"], [user_id]
        if cat_filter:
            conditions.append("LOWER(c.name) LIKE ?")
            params.append(f"%{cat_filter.lower()}%")
        if ym:
            conditions.append("e.date LIKE ?")
            params.append(f"{ym}%")
        where = "WHERE " + " AND ".join(conditions)
        rows = conn.execute(f"""
            SELECT e.id, e.name, e.amount, e.date, c.name AS category_name
            FROM expenses e LEFT JOIN categories c ON e.category_id=c.id
            {where} ORDER BY e.id DESC LIMIT ?
        """, params + [limit]).fetchall()
    return {"expenses": [dict(r) for r in rows], "count": len(rows)}


def _run_list_categories(args: dict, user_id: int) -> dict:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT id, name, is_system FROM categories WHERE is_system=1 OR user_id=? ORDER BY is_system DESC, name",
            (user_id,)
        ).fetchall()
    return {"categories": [dict(r) for r in rows]}


def _run_set_budget(args: dict, user_id: int) -> dict:
    ym, total = args["year_month"], args["total_amount"]
    with get_db() as conn:
        existing = conn.execute(
            "SELECT id FROM budgets WHERE user_id=? AND year_month=?", (user_id, ym)
        ).fetchone()
        if existing:
            conn.execute("UPDATE budgets SET total_amount=? WHERE user_id=? AND year_month=?",
                         (total, user_id, ym))
        else:
            conn.execute("INSERT INTO budgets (user_id, year_month, total_amount) VALUES (?,?,?)",
                         (user_id, ym, total))
        conn.commit()
    return {"ok": True, "year_month": ym, "total_amount": total}


def _run_set_category_budget(args: dict, user_id: int) -> dict:
    ym, cat_name, amount = args["year_month"], args["category"].strip(), args["amount"]
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM categories WHERE (is_system=1 OR user_id=?) AND LOWER(name)=LOWER(?)",
            (user_id, cat_name)
        ).fetchone()
        if not row:
            row = conn.execute(
                "SELECT id FROM categories WHERE (is_system=1 OR user_id=?) AND LOWER(name) LIKE ?",
                (user_id, f"%{cat_name.lower()}%")
            ).fetchone()
        if not row:
            return {"error": f"Category '{cat_name}' not found"}
        category_id = row["id"]
        budget = conn.execute(
            "SELECT id FROM budgets WHERE user_id=? AND year_month=?", (user_id, ym)
        ).fetchone()
        if not budget:
            return {"error": f"No budget for {ym}. Create a total budget first."}
        conn.execute("""
            INSERT INTO budget_categories (budget_id, category_id, amount)
            VALUES (?,?,?)
            ON CONFLICT(budget_id, category_id) DO UPDATE SET amount=excluded.amount
        """, (budget["id"], category_id, amount))
        conn.commit()
    return {"ok": True, "year_month": ym, "category": cat_name, "amount": amount}


def _run_get_budget(args: dict, user_id: int) -> dict:
    ym = args["year_month"]
    with get_db() as conn:
        budget = conn.execute(
            "SELECT * FROM budgets WHERE user_id=? AND year_month=?", (user_id, ym)
        ).fetchone()
        if not budget:
            return {"year_month": ym, "exists": False, "message": f"No budget set for {ym}."}
        budget = dict(budget)

        cats = conn.execute("""
            SELECT c.name AS category, bc.amount AS planned
            FROM budget_categories bc JOIN categories c ON bc.category_id=c.id
            WHERE bc.budget_id=? ORDER BY bc.amount DESC
        """, (budget["id"],)).fetchall()

        spent_rows = conn.execute("""
            SELECT c.name AS category, COALESCE(SUM(e.amount),0) AS spent
            FROM expenses e JOIN categories c ON e.category_id=c.id
            WHERE e.user_id=? AND e.date LIKE ?
            GROUP BY c.id
        """, (user_id, f"{ym}%")).fetchall()
        spent_map = {r["category"]: r["spent"] for r in spent_rows}

        total_spent = conn.execute(
            "SELECT COALESCE(SUM(amount),0) AS t FROM expenses WHERE user_id=? AND date LIKE ?",
            (user_id, f"{ym}%")
        ).fetchone()["t"]

        category_rows = []
        for c in cats:
            planned  = c["planned"]
            spent    = spent_map.get(c["category"], 0)
            pct      = round(spent / planned * 100, 1) if planned > 0 else None
            category_rows.append({
                "category": c["category"], "planned": planned, "spent": spent,
                "remaining": planned - spent, "percent_used": pct,
                "status": "over" if spent > planned else ("warning" if pct and pct >= 80 else "ok"),
            })

        total_planned   = budget["total_amount"]
        allocated_names = {c["category"] for c in cats}
        unplanned = [
            {"category": cat, "spent": amt}
            for cat, amt in spent_map.items()
            if cat not in allocated_names and amt > 0
        ]

        budget.update({
            "exists": True,
            "total_spent": total_spent,
            "total_remaining": total_planned - total_spent,
            "percent_used": round(total_spent / total_planned * 100, 1) if total_planned > 0 else 0,
            "categories": category_rows,
        })
        if unplanned:
            budget["unplanned_spending"] = unplanned
    return budget


TOOL_FNS = {
    "add_expenses":        _run_add_expenses,
    "get_spending_summary":_run_get_spending_summary,
    "get_recent_expenses": _run_get_recent_expenses,
    "list_categories":     _run_list_categories,
    "set_budget":          _run_set_budget,
    "set_category_budget": _run_set_category_budget,
    "get_budget":          _run_get_budget,
}


# ── System prompt ─────────────────────────────────────────────────────────────

SYSTEM_PROMPT = f"""You are a smart, friendly personal finance assistant built into an expense tracker app.
Today is {date.today().isoformat()}.

You can:
- Add one OR multiple expenses at once (always use add_expenses — never pretend to add without using it)
- Analyze spending patterns and give insights
- Show the planned budget and compare it to actual spending
- Help set up monthly budgets and category allocations
- Answer questions about past expenses

Guidelines:
- Be concise and direct. Use bullet points or short lists when listing multiple items.
- When the user mentions buying things or spending money, immediately use add_expenses.
- Infer the category from context if not stated.
- When asked about budget plan, budget status, or "what did I plan to spend", always call get_budget.
  It returns both plan AND actual — use it to give a clear comparison.
- When presenting budget: show total (spent vs planned), then list categories with status.
  Flag unplanned spending separately.
- Format dollar amounts as $X.XX.
- After adding expenses or setting a budget, briefly confirm what was done.
- Never make up data — always use tools.
"""


# ── Main streaming function ───────────────────────────────────────────────────

def stream_chat(messages: list, user_id: int):
    """Yields SSE lines. Streams text tokens, tool results, and done signal."""
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

        tool_calls_raw: dict = {}
        text_buffer = ""
        finish_reason = None
        assistant_msg: dict = {"role": "assistant", "content": None}

        for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue
            finish_reason = chunk.choices[0].finish_reason or finish_reason

            if delta.content:
                text_buffer += delta.content
                yield f"data: {json.dumps({'type': 'text', 'content': delta.content})}\n\n"

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

        if tool_calls_raw:
            assistant_msg["tool_calls"] = [
                {"id": v["id"], "type": "function",
                 "function": {"name": v["name"], "arguments": v["args"]}}
                for v in tool_calls_raw.values()
            ]

        full_messages.append(assistant_msg)

        if not tool_calls_raw or finish_reason == "stop":
            break

        # Execute tools
        for v in tool_calls_raw.values():
            fn = TOOL_FNS.get(v["name"])
            try:
                result = fn(json.loads(v["args"]), user_id) if fn else {"error": f"Unknown: {v['name']}"}
            except Exception as e:
                result = {"error": str(e)}

            yield f"data: {json.dumps({'type': 'tool_result', 'name': v['name'], 'result': result})}\n\n"
            full_messages.append({
                "role": "tool",
                "tool_call_id": v["id"],
                "content": json.dumps(result),
            })

    yield f"data: {json.dumps({'type': 'done'})}\n\n"
