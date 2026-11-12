"""Quick expenses in the daisyUI frontend: entry, averages parity, filters, edit and delete."""
from datetime import date, timedelta

from playwright.sync_api import expect

from models import Expense, JournalEntry
from test_daisyui_bills import create
from test_daisyui_test import HX, prototype, prototype_server

Q = '?view=expenses'


def add(client, amount, description, category='Groceries', days_ago=0, **extra):
    data = {'amount': amount, 'description': description, 'category': category,
            'date': (date.today() - timedelta(days=days_ago)).isoformat(), 'payment_method': 'Card', 'currency': 'AUD', **extra}
    return client.post('/app/expenses/create' + Q, headers=HX, data=data)


def test_entry_and_summaries_match_original(prototype):
    db, client, ids = prototype
    response = add(client, '12.50', 'Coffee beans')
    assert response.status_code == 200 and 'Added Coffee beans.' in response.text
    add(client, '80.00', 'Fuel', 'Transport & Fuel', days_ago=10, payment_method='Cash')
    add(client, '4.20', '', 'Snacks', days_ago=40, note='vending machine')
    assert db.query(JournalEntry).filter_by(action='Added Expense').count() == 3
    page = client.get('/app/expenses')
    original = client.get('/expenses')
    assert page.status_code == original.status_code == 200
    ours, theirs = page.context['summaries'][0], original.context['currency_summaries'][0]
    for key in ('total_spending', 'weekly_avg', 'monthly_avg', 'yearly_avg', 'past_7_days_total', 'this_month_total', 'ytd_total'):
        assert ours[key] == theirs[key]
    assert [row['name'] for row in ours['category_breakdown']] == ['Transport & Fuel', 'Groceries', 'Snacks']
    for amount, text in [('0', 'greater than zero'), ('1.234', ''), ('', '')]:
        bad = add(client, amount, 'Nope')
        assert bad.status_code == 400 and bad.headers['HX-Retarget'] == '#page-feedback'
    assert add(client, '5', 'Bad date', date='2026-02-30').status_code == 400
    assert add(client, '5', 'Bad currency', currency='XYZ').status_code == 400
    assert db.query(Expense).count() == 3


def test_include_bills_toggle_and_filters(prototype):
    db, client, ids = prototype
    add(client, '12.50', 'Coffee beans')
    add(client, '80.00', 'Fuel', 'Transport & Fuel', days_ago=3, payment_method='Cash')
    add(client, '30.00', 'Lunch with team', 'Food & Dining', days_ago=20, note='Friday')
    create(client, 'Rent', 10, amount='2000.00')
    plain = client.get('/app/expenses')
    with_bills = client.get('/app/expenses?include_bills=1')
    assert not plain.context['include_bills'] and with_bills.context['include_bills']
    assert with_bills.context['summaries'][0]['combined_monthly_avg'] > plain.context['summaries'][0]['monthly_avg']
    assert 'Including bill payments' in with_bills.text
    assert [e.description for e in client.get('/app/expenses?q=friday').context['rows']] == ['Lunch with team']
    assert [e.description for e in client.get('/app/expenses?method=Cash').context['rows']] == ['Fuel']
    assert [e.description for e in client.get('/app/expenses?category=Food+%26+Dining').context['rows']] == ['Lunch with team']
    start = (date.today() - timedelta(days=5)).isoformat()
    assert {e.description for e in client.get(f'/app/expenses?start={start}').context['rows']} == {'Coffee beans', 'Fuel'}
    largest = client.get('/app/expenses?sort=largest')
    assert [e.description for e in largest.context['rows']] == ['Fuel', 'Lunch with team', 'Coffee beans']
    assert largest.context['row_totals']['AUD']['total'] == 122.5
    assert client.get('/app/expenses?start=yesterday').status_code == 400


def test_edit_and_delete_keep_filters(prototype):
    db, client, ids = prototype
    add(client, '12.50', 'Coffee beans')
    expense = db.query(Expense).one()
    query = '?category=Groceries&view=expenses'
    data = {'amount': '13.00', 'description': 'Coffee beans 1kg', 'category': 'Groceries', 'date': date.today().isoformat(),
            'payment_method': 'Transfer', 'currency': 'AUD', 'note': 'Roaster'}
    response = client.post(f'/app/expenses/{expense.id}/update{query}', headers=HX, data=data)
    assert response.status_code == 200 and 'Expense updated.' in response.text and 'All categories' in response.text
    db.expire_all()
    expense = db.get(Expense, expense.id)
    assert expense.amount == 13.0 and expense.payment_method == 'Transfer' and expense.note == 'Roaster'
    assert client.post(f'/app/expenses/{expense.id}/update{query}', headers=HX, data={**data, 'amount': '-1'}).status_code == 400
    redirect = client.post(f'/app/expenses/{expense.id}/update{query}', data=data, follow_redirects=False)
    assert redirect.headers['location'] == '/app/expenses?category=Groceries'
    expense_id = expense.id
    deleted = client.post(f'/app/expenses/{expense_id}/delete{query}', headers=HX)
    assert deleted.status_code == 200 and 'Expense deleted.' in deleted.text
    db.expire_all()
    assert db.query(Expense).count() == 0
    assert client.post(f'/app/expenses/{expense_id}/delete{query}', headers=HX).status_code == 404


def test_browser_quick_entry_keeps_focus(prototype_server, page):
    origin, (db, client, ids) = prototype_server
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app')
    page.locator('summary[aria-label="More navigation"]').click()
    page.get_by_role('link', name='Quick expenses').click()
    page.get_by_role('spinbutton', name='Amount').fill('9.80')
    page.get_by_role('textbox', name='Description').fill('Sandwich')
    page.get_by_role('combobox', name='Category', exact=True).fill('Food & Dining')
    page.get_by_role('button', name='Add expense').click()
    expect(page.locator('#page-feedback')).to_contain_text('Added Sandwich.')
    expect(page.get_by_role('spinbutton', name='Amount')).to_be_focused()
    expect(page.get_by_role('spinbutton', name='Amount')).to_have_value('')
    expect(page.locator('.expense-row')).to_contain_text('Sandwich')
    page.get_by_role('link', name='Include bills').click()
    expect(page).to_have_url(origin + '/app/expenses?include_bills=1')
    page.locator('.category-bars a').first.click()
    expect(page.get_by_role('combobox', name='Filter by category')).to_have_value('Food & Dining')
    assert not errors
