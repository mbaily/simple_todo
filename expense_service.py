"""Expense validation and currency-separated financial summaries."""
from datetime import timedelta

from models import BillPayment, BillAdjustment, _to_date
from billing_service import money_cents, stored_amount, monthly_budget


def expense_amount(value, label='Expense amount'):
    try:
        cents = money_cents(value)
    except (ValueError, TypeError) as error:
        raise ValueError(f'{label}: {error}') from None
    if cents == 0:
        raise ValueError(f'{label}: Enter an amount greater than zero.')
    return stored_amount(cents, label)


def validate_expense_amounts(data):
    for item in data.get('expenses', []):
        if 'amount' in item:
            label = f'Expense #{item["id"]} amount' if item.get('id') is not None else 'Imported expense amount'
            item['amount'] = expense_amount(item['amount'], label)


def group_by_currency(records):
    groups = {}
    for record in records:
        owner = record.bill if isinstance(record, (BillPayment, BillAdjustment)) else record
        currency = (owner.currency or 'AUD').strip().upper() or 'AUD'
        groups.setdefault(currency, []).append(record)
    return groups


def spending_summaries(expenses, bills, payments, refunds, today):
    collections = [group_by_currency(records) for records in (expenses, bills, payments, refunds)]
    currencies = sorted(set().union(*collections)) or ['AUD']
    summaries = []
    for currency in currencies:
        rows = [collection.get(currency, []) for collection in collections]
        summary = _spending_summary(*rows, today)
        records = next((records for records in rows if records), [])
        owner = records[0] if records else None
        if isinstance(owner, (BillPayment, BillAdjustment)):
            owner = owner.bill
        summary.update(currency=currency, currency_symbol=owner.currency_symbol if owner else '$')
        summaries.append(summary)
    return summaries


def _spending_summary(expenses, bills, payments, refunds, today):
    total_spending = sum(e.amount for e in expenses if e.amount is not None)
    total_count = len(expenses)
    avg_per_txn = total_spending / total_count if total_count else 0.0
    earliest = min((_to_date(e.date) for e in expenses if _to_date(e.date)), default=today)
    days_tracked = max(1, (today - earliest).days + 1)
    weekly_avg = total_spending / max(1.0, days_tracked / 7.0)
    monthly_avg = total_spending / max(1.0, days_tracked / 30.417)
    yearly_avg = monthly_avg * 12.0
    seven_day_start = today - timedelta(days=6)
    month_start = today.replace(day=1)
    year_start = today.replace(month=1, day=1)

    def expense_total(start):
        return sum(e.amount for e in expenses if e.amount and _to_date(e.date)
                   and start <= _to_date(e.date) <= today)

    def cash_total(start=None):
        receipts = sum(p.amount_paid for p in payments if p.amount_paid is not None
                       and _to_date(p.paid_date) <= today
                       and (start is None or _to_date(p.paid_date) >= start))
        refunded = sum(a.amount_cents for a in refunds if _to_date(a.effective_date) <= today
                       and (start is None or _to_date(a.effective_date) >= start))
        return receipts - refunded / 100

    past_7_days_total = expense_total(seven_day_start)
    this_month_total = expense_total(month_start)
    ytd_total = expense_total(year_start)
    bills_monthly = sum(monthly_budget(bill) for bill in bills)
    bills_weekly = bills_monthly / 4.33 if bills_monthly > 0 else 0.0
    bills_yearly = bills_monthly * 12.0
    bills_past_7_days = cash_total(seven_day_start)
    bills_this_month = cash_total(month_start)
    bills_ytd = cash_total(year_start)
    all_time_bills = cash_total()

    categories = {}
    for expense in expenses:
        category = (expense.category or 'Other').strip() or 'Other'
        row = categories.setdefault(category, dict(name=category, total=0.0, count=0, percent=0.0))
        row['total'] += expense.amount or 0.0
        row['count'] += 1
    category_breakdown = sorted(categories.values(), key=lambda row: row['total'], reverse=True)
    if total_spending > 0:
        for row in category_breakdown:
            row['percent'] = row['total'] / total_spending * 100

    return dict(
        total_spending=total_spending, total_count=total_count, avg_per_txn=avg_per_txn,
        weekly_avg=weekly_avg, past_7_days_total=past_7_days_total,
        monthly_avg=monthly_avg, this_month_total=this_month_total,
        yearly_avg=yearly_avg, ytd_total=ytd_total, category_breakdown=category_breakdown,
        bills_monthly=bills_monthly, bills_weekly=bills_weekly, bills_yearly=bills_yearly,
        bills_past_7_days=bills_past_7_days, bills_this_month=bills_this_month,
        bills_ytd=bills_ytd, all_time_bills=all_time_bills, active_bills_count=len(bills),
        combined_weekly_avg=weekly_avg + bills_weekly,
        combined_past_7_days=past_7_days_total + bills_past_7_days,
        combined_monthly_avg=monthly_avg + bills_monthly,
        combined_this_month=this_month_total + bills_this_month,
        combined_yearly_avg=yearly_avg + bills_yearly,
        combined_ytd=ytd_total + bills_ytd,
        combined_total_spending=total_spending + all_time_bills,
    )
