"""Bills dashboard for the compact daisyUI frontend.

Grouping and estimates come from main._bills_dashboard_data, and payments,
invoice confirmation and activation call the original routes in-process, so
ledger rules, request-token duplicate protection and journal entries are
unchanged. Validation errors from those routes are shown inline.
"""
from datetime import datetime
import inspect
import typing

from fastapi import Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic.fields import FieldInfo
from sqlalchemy.orm import Session

from models import Bill, BillPayment
from daisyui_test_http import wants_fragment

TRUE = {"1", "true", "yes", "on"}
FREQUENCIES = {"weekly": "Weekly", "fortnightly": "Fortnightly", "monthly": "Monthly", "bimonthly": "Every 2 months",
               "quarterly": "Quarterly", "yearly": "Yearly", "custom": "Every N days"}
STRATEGIES = {"fixed": "Fixed amount", "rolling_avg": "Varies: rolling average", "last_payment": "Varies: same as last payment"}
CURRENCIES = {"AUD": "AUD $", "USD": "USD $", "EUR": "EUR €", "GBP": "GBP £", "NZD": "NZD $", "CAD": "CAD $"}
# History-page operations: name -> (action key, id field checked against the bill, success message).
OPERATIONS = {
    "invoice": ("save_invoice", None, "Invoice saved."),
    "pay": ("pay", None, "Payment recorded."),
    "extra-payment": ("extra_payment", None, "Extra payment recorded."),
    "adjustment": ("save_adjustment", None, "Adjustment saved."),
    "adjustment-delete": ("delete_adjustment", None, "Adjustment deleted."),
    "statement": ("save_statement", None, "Supplier balance recorded."),
    "statement-delete": ("delete_statement", None, "Supplier balance deleted."),
    "payment-edit": ("edit_payment", "payment_id", "Payment updated."),
    "payment-delete": ("delete_payment", "payment_id", "Payment deleted."),
    "payment-note": ("payment_note", "payment_id", "Note added."),
}


def call_with_form(fn, form, **fixed):
    """Call an original form route with values from a submitted form.

    Parameters follow the route's own declaration: omitted fields take the
    declared default, form_text dependencies receive None, and ints and bools
    are converted as FastAPI would.
    """
    hints = typing.get_type_hints(fn)
    kwargs = {}
    for name, param in inspect.signature(fn).parameters.items():
        if name in fixed:
            kwargs[name] = fixed[name]
            continue
        default, present = param.default, name in form
        value = form.get(name) if present else None
        if type(default).__name__ == "Depends":
            kwargs[name] = value
            continue
        if not present:
            if isinstance(default, FieldInfo):
                if default.is_required():
                    raise HTTPException(400, f"The form is missing {name.replace('_', ' ')}. Reload the page and try again.")
                kwargs[name] = default.default
            elif default is inspect.Parameter.empty:
                raise HTTPException(400, f"The form is missing {name.replace('_', ' ')}. Reload the page and try again.")
            else:
                kwargs[name] = default
            continue
        kind = hints.get(name, str)
        options = [arg for arg in typing.get_args(kind) if arg is not type(None)] or [kind]
        if int in options and bool not in options:
            try:
                kwargs[name] = int(value) if str(value).strip() else None
            except ValueError:
                raise HTTPException(400, "The form is invalid. Reload the page and try again.")
        elif bool in options:
            kwargs[name] = str(value).strip().lower() in TRUE
        else:
            kwargs[name] = value
    return fn(**kwargs)

SECTIONS = [
    ("overdue", "Overdue", "bell"), ("due_today", "Due today", "bell"), ("upcoming", "Upcoming", "calendar"),
    ("covered_early", "Paid for the current cycle", "check"), ("future_estimates", "Future estimates", "clock"),
    ("unscheduled", "No due date", "info"),
]


def bills_view(request):
    return request.query_params.get("view") in {"bills", "bill"}


def attach_bill_routes(router, *, templates, get_db, actions, feedback, success):
    def base(request):
        return request.scope.get("root_path", "") + "/app"

    def show_inactive(request):
        return 1 if request.query_params.get("show_inactive") in {"1", "true"} else 0

    def render_bills(request, db, message=None, status_code=200):
        if request.query_params.get("view") == "bill":
            return render_history(request, db, message, status_code)
        inactive = show_inactive(request)
        data = actions["dashboard"](db, inactive)
        url = base(request)
        context = dict(
            request=request, prototype_url=url, bills_page=True,
            query="?view=bills" + ("&show_inactive=1" if inactive else ""),
            workspace_template="daisyui_test/_bills_workspace.html", page_title="Bills",
            page_url=url + "/bills" + ("?show_inactive=1" if inactive else ""), sections=SECTIONS,
            bill_total=sum(len(data[key]) for key, _, _ in SECTIONS), **data,
            message=message, is_error=status_code >= 400,
        )
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, context["workspace_template"] if partial else "daisyui_test/index.html", context,
            status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store"},
        )

    def page(request, ctx, message=None, status_code=200, headers=None):
        partial = wants_fragment(request)
        return templates.TemplateResponse(
            request, ctx["workspace_template"] if partial else "daisyui_test/index.html",
            {**ctx, "message": message, "is_error": status_code >= 400}, status_code=status_code,
            headers={"Vary": "HX-Request, HX-History-Restore-Request", "Cache-Control": "no-store", **(headers or {})},
        )

    def history_id(request):
        raw = request.path_params.get("bill_id", request.query_params.get("bill_id"))
        try:
            return int(raw)
        except (TypeError, ValueError):
            raise HTTPException(400, "Invalid bill")

    def render_history(request, db, message=None, status_code=200):
        bill = existing(db, history_id(request))
        payments = (db.query(BillPayment).filter(BillPayment.bill_id == bill.id)
                    .order_by(BillPayment.due_date.desc(), BillPayment.id.desc()).all())
        paid = [payment.amount_paid for payment in payments if payment.amount_paid is not None]
        refunds = sum(item.amount_cents for item in bill.adjustments if item.kind == "refund") / 100
        url = base(request)
        return page(request, dict(
            request=request, prototype_url=url, bills_page=True, query=f"?view=bill&bill_id={bill.id}",
            workspace_template="daisyui_test/_bill_history_workspace.html",
            page_title=f"{bill.name} · Bills", page_url=url + f"/bills/{bill.id}",
            bill=bill, payments=payments, total_paid=sum(paid), avg_amount=sum(paid) / len(paid) if paid else None,
            refunds_total=refunds, statement_comparisons=actions["statement_comparisons"](bill),
            adjustment_types=actions["adjustment_types"], today_iso=datetime.now().strftime("%Y-%m-%d"),
            frequencies=FREQUENCIES, strategies=STRATEGIES,
        ), message, status_code)

    def render_editor(request, db, bill, message=None, status_code=200):
        url = base(request)
        return page(request, dict(
            request=request, prototype_url=url, bills_page=True, query="?view=bills",
            workspace_template="daisyui_test/_bill_edit_workspace.html",
            page_title=("Edit " + bill.name if bill else "New bill") + "",
            page_url=url + (f"/bills/{bill.id}/edit" if bill else "/bills/new"), bill=bill,
            frequencies=FREQUENCIES, strategies=STRATEGIES, currencies=CURRENCIES,
        ), message, status_code)

    def call_original(request, db, call, message):
        """Run an original bill route; its 400 errors become inline feedback."""
        try:
            result = call()
        except HTTPException as exc:
            db.rollback()
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        if result.status_code == 409:
            ctx = getattr(result, "context", {}) or {}
            existing, bill = ctx.get("existing"), ctx.get("bill")
            if ctx.get("same_number"):
                text = "That supplier invoice number is already recorded for this bill. Check the number, or open the history to edit the existing invoice."
            else:
                detail = ""
                if existing is not None and bill is not None:
                    detail = f" ({bill.currency_symbol}{existing.amount_cents / 100:.2f}, due {existing.due_date_iso or 'unknown'})"
                text = (f"This matches an invoice that is already recorded{detail}, so nothing was added. "
                        "If the provider really issued a separate invoice, tick “Record as a separate invoice” and save again.")
            return feedback(request, db, "user", None, text, 409)
        if not message:
            return result
        return success(request, db, "user", None, message)

    def existing(db, bill_id):
        bill = db.get(Bill, bill_id)
        if bill is None:
            raise HTTPException(404, "This bill no longer exists.")
        return bill

    @router.get("/bills", response_class=HTMLResponse)
    def bills(request: Request, db: Session = Depends(get_db)):
        return render_bills(request, db)

    @router.get("/bills/new", response_class=HTMLResponse)
    def new_bill(request: Request, db: Session = Depends(get_db)):
        return render_editor(request, db, None)

    @router.post("/bills/new", response_class=HTMLResponse)
    async def create_bill(request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        if not str(form.get("name", "")).strip():
            return feedback(request, db, "user", None, "Enter a name for the bill.")
        result = call_original(request, db, lambda: call_with_form(actions["create"], form, db=db), "")
        if result.status_code >= 400:
            return result
        created = db.query(Bill).order_by(Bill.id.desc()).first()
        return go_to(request, db, f"/bills/{created.id}", render_history, {"bill_id": created.id}, f"Created {created.name}.")

    @router.get("/bills/{bill_id}", response_class=HTMLResponse)
    def history(request: Request, bill_id: int, db: Session = Depends(get_db)):
        return render_history(request, db)

    @router.get("/bills/{bill_id}/edit", response_class=HTMLResponse)
    def edit_bill(request: Request, bill_id: int, db: Session = Depends(get_db)):
        return render_editor(request, db, existing(db, bill_id))

    @router.post("/bills/{bill_id}/edit", response_class=HTMLResponse)
    async def update_bill(request: Request, bill_id: int, db: Session = Depends(get_db)):
        try:
            bill = existing(db, bill_id)
        except HTTPException as exc:
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        form = await request.form()
        if not str(form.get("name", "")).strip():
            return feedback(request, db, "user", None, "Enter a name for the bill.")
        result = call_original(request, db, lambda: call_with_form(actions["update"], form, bill_id=bill_id, db=db), "")
        if result.status_code >= 400:
            return result
        return go_to(request, db, f"/bills/{bill_id}", render_history, {"bill_id": bill_id}, f"Saved {bill.name}.")

    @router.post("/bills/{bill_id}/delete", response_class=HTMLResponse)
    def delete_bill(request: Request, bill_id: int, db: Session = Depends(get_db)):
        try:
            bill = existing(db, bill_id)
        except HTTPException as exc:
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        name = bill.name
        actions["delete"](bill_id=bill_id, db=db)
        return go_to(request, db, "/bills", render_bills, {}, f"Deleted {name} and its history.")

    @router.post("/bills/{bill_id}/do/{operation}", response_class=HTMLResponse)
    async def operate(request: Request, bill_id: int, operation: str, db: Session = Depends(get_db)):
        if operation not in OPERATIONS:
            return feedback(request, db, "user", None, "Unknown bill action.", 404)
        try:
            bill = existing(db, bill_id)
        except HTTPException as exc:
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        key, owned, message = OPERATIONS[operation]
        form = await request.form()
        fixed = {"bill_id": bill_id, "db": db, "request": request}
        if owned:
            try:
                payment = db.get(BillPayment, int(form.get(owned, "")))
            except ValueError:
                payment = None
            if payment is None or payment.bill_id != bill.id:
                return feedback(request, db, "user", None, "This payment no longer belongs to the bill. Refresh the page.", 404)
            fixed[owned] = payment.id
        for name in ("adjustment_id", "statement_id"):
            if operation.endswith("-delete") and name.split("_")[0] in operation:
                try:
                    fixed[name] = int(form.get(name, ""))
                except ValueError:
                    return feedback(request, db, "user", None, "The form is invalid. Reload the page and try again.")
        fn = actions[key]
        accepted = inspect.signature(fn).parameters
        fixed = {name: value for name, value in fixed.items() if name in accepted}
        return call_original(request, db, lambda: call_with_form(fn, form, **fixed), message)

    @router.post("/bills/{bill_id}/late-override", response_class=HTMLResponse)
    def late_override(request: Request, bill_id: int, payment_id: int = Form(0), overridden: bool = Form(False),
                      db: Session = Depends(get_db)):
        payment = db.get(BillPayment, payment_id)
        if payment is None or payment.bill_id != bill_id:
            return feedback(request, db, "user", None, "This payment no longer belongs to the bill. Refresh the page.", 404)
        # Set the requested state so a retried request cannot flip it back.
        if bool(payment.is_late_overridden) != overridden:
            actions["toggle_late"](payment_id=payment_id, db=db)
        return success(request, db, "user", None, "Marked as on time." if overridden else "Late marking restored.")

    def go_to(request, db, path, renderer, path_params, message):
        url = base(request) + path
        if request.headers.get("HX-Request") != "true":
            return RedirectResponse(url, status_code=303)
        scope = dict(request.scope)
        query = "view=bills" if path == "/bills" else f"view=bill&bill_id={path_params['bill_id']}"
        scope.update(path_params=path_params, query_string=query.encode())
        response = renderer(Request(scope), db, message)
        response.headers["HX-Push-Url"] = url
        return response

    @router.post("/bills/{bill_id}/pay", response_class=HTMLResponse)
    def pay(request: Request, bill_id: int, amount_paid: str = Form(""), paid_date: str = Form(""),
            actual_due_date: str = Form(""), note: str = Form(""), next_due_override: str = Form(""),
            invoice_id: int | None = Form(None), request_token: str = Form(""), db: Session = Depends(get_db)):
        try:
            bill = existing(db, bill_id)
        except HTTPException as exc:
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        name = bill.name
        return call_original(request, db, lambda: actions["pay"](
            bill_id=bill_id, amount_paid=amount_paid.strip() or None, paid_date=paid_date, actual_due_date=actual_due_date,
            note=note, next_due_override=next_due_override, invoice_id=invoice_id, request_token=request_token or None, db=db,
        ), f"Payment recorded for {name}.")

    @router.post("/bills/{bill_id}/confirm-invoice", response_class=HTMLResponse)
    def confirm(request: Request, bill_id: int, actual_due_date: str = Form(""), actual_amount: str = Form(""),
                amount_type: str = Form("gross"), request_token: str = Form(""), allow_duplicate: bool = Form(False),
                invoice_number: str = Form(""), invoice_date: str = Form(""), invoice_target: str = Form("new"),
                credit_applied: str | None = Form(None), db: Session = Depends(get_db)):
        try:
            bill = existing(db, bill_id)
        except HTTPException as exc:
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        if not actual_due_date.strip() and not actual_amount.strip():
            return feedback(request, db, "user", None, "Enter the due date, the amount, or both from the bill.")
        if amount_type not in {"gross", "net"}:
            return feedback(request, db, "user", None, "Choose how the amount is shown on the invoice.")
        name = bill.name
        return call_original(request, db, lambda: actions["confirm"](
            request=request, bill_id=bill_id, actual_due_date=actual_due_date, actual_amount=actual_amount,
            amount_type=amount_type, request_token=request_token or None, allow_duplicate=allow_duplicate,
            invoice_number=invoice_number, invoice_date=invoice_date, invoice_target=invoice_target or "new",
            credit_applied=credit_applied, db=db,
        ), f"Confirmed the bill details for {name}.")

    @router.post("/bills/{bill_id}/active", response_class=HTMLResponse)
    def set_active(request: Request, bill_id: int, active: bool = Form(False), db: Session = Depends(get_db)):
        try:
            bill = existing(db, bill_id)
        except HTTPException as exc:
            return feedback(request, db, "user", None, str(exc.detail), exc.status_code)
        name = bill.name
        # Set the requested state so a retried request cannot flip it back.
        if bool(bill.is_active) != active:
            actions["toggle_active"](bill_id=bill_id, show_inactive=0, db=db)
        return success(request, db, "user", None, f"{'Reactivated' if active else 'Deactivated'} {name}.")

    return render_bills
