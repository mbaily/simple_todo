"""Quick expenses for the compact daisyUI frontend: entry, averages, categories and a filtered ledger.

Summaries come from expense_service.spending_summaries (shared with the original
pages), and create, update and delete call the original routes in-process.
"""
from datetime import date, datetime
from urllib.parse import urlencode

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session, joinedload

from models import Bill, BillAdjustment, BillPayment, Expense
from daisyui_test_http import wants_fragment

PAGE_LIMIT = 200
METHODS = ["Card", "Cash", "Direct Debit", "Transfer", "Other"]
SUGGESTED = ["Food & Dining", "Groceries", "Transport & Fuel", "Shopping", "Entertainment", "Bills & Utilities",
             "Health & Medical", "Personal Care", "Travel", "Work & Business", "Other"]
SORTS = {"newest": "Newest first", "oldest": "Oldest first", "largest": "Largest first", "smallest": "Smallest first"}
CURRENCIES = ["AUD", "USD", "EUR", "GBP", "NZD", "CAD"]


def expenses_view(request):
    return request.query_params.get("view") == "expenses"


def as_date(value):
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


def attach_expense_routes(router, *, templates, get_db, actions, feedback, success):
    def base(request):
        return request.scope.get("root_path", "") + "/app"

    def filters(request):
        params = request.query_params
        values = {key: (params.get(key) or "").strip()[:200] for key in ("q", "category", "method", "start", "end", "sort")}
        for key in ("start", "end"):
            if values[key]:
                try:
                    datetime.strptime(values[key], "%Y-%m-%d")
                except ValueError:
                    raise HTTPException(400, "Invalid date filter")
        if values["sort"] not in SORTS:
            values["sort"] = "newest"
        values["include_bills"] = params.get("include_bills") in {"1", "true", "yes"}
        return values

    def render_expenses(request, db, message=None, status_code=200):
        chosen = filters(request)
        today = date.today()
        expenses = db.query(Expense).order_by(Expense.date.desc(), Expense.id.desc()).all()
        recorded = [e for e in expenses if as_date(e.date) and as_date(e.date) <= today]
        bills = db.query(Bill).filter(Bill.is_active == True).all()
        payments = db.query(BillPayment).options(joinedload(BillPayment.bill)).all()
        refunds = db.query(BillAdjustment).options(joinedload(BillAdjustment.bill)).filter(BillAdjustment.kind == "refund").all()
        summaries = actions["summaries"](recorded, bills, payments, refunds, today)

        rows = expenses
        if chosen["q"]:
            needle = chosen["q"].lower()
            rows = [e for e in rows if needle in (e.description or "").lower() or needle in (e.note or "").lower()]
        if chosen["category"]:
            rows = [e for e in rows if (e.category or "Other").strip() == chosen["category"]]
        if chosen["method"]:
            rows = [e for e in rows if (e.payment_method or "") == chosen["method"]]
        if chosen["start"]:
            start = datetime.strptime(chosen["start"], "%Y-%m-%d").date()
            rows = [e for e in rows if as_date(e.date) and as_date(e.date) >= start]
        if chosen["end"]:
            end = datetime.strptime(chosen["end"], "%Y-%m-%d").date()
            rows = [e for e in rows if as_date(e.date) and as_date(e.date) <= end]
        key = {"newest": lambda e: (as_date(e.date) or date.min, e.id), "oldest": lambda e: (as_date(e.date) or date.min, e.id),
               "largest": lambda e: (e.amount or 0, e.id), "smallest": lambda e: (e.amount or 0, e.id)}[chosen["sort"]]
        rows = sorted(rows, key=key, reverse=chosen["sort"] in {"newest", "largest"})
        totals = {}
        for expense in rows:
            entry = totals.setdefault(expense.currency or "AUD", {"symbol": expense.currency_symbol, "total": 0.0})
            entry["total"] += expense.amount or 0.0

        active = {k: v for k, v in chosen.items() if v and k not in {"include_bills"} and not (k == "sort" and v == "newest")}
        state = {**active, **({"include_bills": 1} if chosen["include_bills"] else {})}
        url = base(request) + "/expenses"
        known = sorted({(e.category or "").strip() for e in expenses if e.category and e.category.strip()})
        context = dict(
            request=request, prototype_url=base(request), expenses_page=True,
            query="?" + urlencode({**state, "view": "expenses"}),
            workspace_template="daisyui_test/_expenses_workspace.html", page_title="Quick expenses",
            page_url=url + ("?" + urlencode(state) if state else ""), expenses_url=url,
            summaries=summaries, include_bills=chosen["include_bills"], filters=chosen, filtering=bool(active.keys() - {"sort"}),
            rows=rows[:PAGE_LIMIT], row_total=len(rows), row_totals=totals, total_count=len(expenses),
            known_categories=known, category_choices=sorted(set(known) | set(SUGGESTED)),
            known_methods=sorted({e.payment_method for e in expenses if e.payment_method} | set(METHODS)),
            methods=METHODS, sorts=SORTS, currencies=CURRENCIES, today_iso=today.isoformat(),
            default_currency=(expenses[0].currency if expenses and expenses[0].currency else "AUD"),
            toggle_bills_url=url + "?" + urlencode({**active, **({} if chosen["include_bills"] else {"include_bills": 1})}),
            message=message, is_error=status_code >= 400,
        )
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, context["workspace_template"] if partial else "daisyui_test/index.html", context,
            status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store"},
        )

    def run(request, db, call, message):
        try:
            call()
        except HTTPException as exc:
            db.rollback()
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        return success(request, db, "user", None, message)

    def check_currency(currency):
        if currency and currency.strip().upper() not in CURRENCIES:
            raise HTTPException(400, "Choose a supported currency.")

    def check_date(value):
        if value.strip():
            try:
                datetime.strptime(value.strip(), "%Y-%m-%d")
            except ValueError:
                raise HTTPException(400, "Choose a valid date.")

    @router.get("/expenses", response_class=HTMLResponse)
    def expenses(request: Request, db: Session = Depends(get_db)):
        return render_expenses(request, db)

    @router.post("/expenses/create", response_class=HTMLResponse)
    def create(request: Request, amount: str = Form(""), description: str = Form(""), category: str = Form(""),
               date_: str = Form("", alias="date"), payment_method: str = Form("Card"), currency: str = Form("AUD"),
               note: str = Form(""), db: Session = Depends(get_db)):
        def call():
            check_currency(currency)
            check_date(date_)
            actions["create"](amount=amount, description=description, category=category, date=date_,
                              payment_method=payment_method, currency=currency, note=note, db=db)
        label = description.strip() or category.strip() or "expense"
        return run(request, db, call, f"Added {label}.")

    @router.post("/expenses/{expense_id}/update", response_class=HTMLResponse)
    def update(request: Request, expense_id: int, amount: str = Form(""), description: str = Form(""),
               category: str = Form(""), date_: str = Form("", alias="date"), payment_method: str = Form("Card"),
               currency: str | None = Form(None), note: str = Form(""), db: Session = Depends(get_db)):
        def call():
            if db.get(Expense, expense_id) is None:
                raise HTTPException(404, "This expense no longer exists.")
            check_currency(currency)
            check_date(date_)
            actions["update"](expense_id=expense_id, amount=amount, description=description, category=category,
                              date=date_, payment_method=payment_method, currency=currency, note=note, return_to=None, db=db)
        return run(request, db, call, "Expense updated.")

    @router.post("/expenses/{expense_id}/delete", response_class=HTMLResponse)
    def delete(request: Request, expense_id: int, db: Session = Depends(get_db)):
        def call():
            if db.get(Expense, expense_id) is None:
                raise HTTPException(404, "This expense no longer exists.")
            actions["delete"](expense_id=expense_id, return_to=None, db=db)
        return run(request, db, call, "Expense deleted.")

    return render_expenses
