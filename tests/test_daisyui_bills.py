"""Bills dashboard in the daisyUI frontend: grouping parity, payments, invoice confirmation and activation."""
from datetime import date, timedelta

from playwright.sync_api import expect

from models import Bill, BillPayment, JournalEntry
from test_daisyui_test import HX, prototype, prototype_server

Q = '?view=bills'


def create(client, name, due_in, amount='120.00', **extra):
    due = (date.today() + timedelta(days=due_in)).isoformat() if due_in is not None else ''
    response = client.post('/bills/create', data={'name': name, 'next_due_date': due, 'expected_amount': amount, **extra},
                           follow_redirects=False)
    assert response.status_code == 303


def bills(db):
    db.expire_all()
    return {bill.name: bill for bill in db.query(Bill).all()}


def test_dashboard_groups_match_original(prototype):
    db, client, ids = prototype
    create(client, 'Water', -3)
    create(client, 'Power', 0, is_due_date_confirmed='true')
    create(client, 'Phone', 5, payee='Telco', category='Utilities', is_auto_pay='true')
    create(client, 'Insurance', 60, amount='900.00')
    create(client, 'Mystery', None, amount='')
    create(client, 'Old gym', 10)
    client.post(f'/bills/{bills(db)["Old gym"].id}/toggle-active', follow_redirects=False)
    full = client.get('/app/bills')
    fragment = client.get('/app/bills', headers=HX)
    original = client.get('/classic/bills')
    assert full.status_code == fragment.status_code == 200 and '<!DOCTYPE html>' not in fragment.text
    for key in ('overdue', 'due_today', 'upcoming', 'covered_early', 'future_estimates', 'unscheduled', 'recent_payments'):
        assert [item.id for item in full.context[key]] == [item.id for item in original.context[key]]
    assert [b.name for b in full.context['overdue']] == ['Water'] and [b.name for b in full.context['unscheduled']] == ['Mystery']
    assert '3 days overdue' in full.text and 'Show inactive (1)' in full.text and 'Old gym' not in full.text
    assert 'Telco' in full.text and 'Auto-pay' in full.text and 'Invoice confirmed' in full.text
    shown = client.get('/app/bills?show_inactive=1')
    assert [b.name for b in shown.context['inactive_bills']] == ['Old gym'] and 'Reactivate' in shown.text


def test_record_payment_and_retry_is_not_duplicated(prototype):
    db, client, ids = prototype
    create(client, 'Water', -3)
    water = bills(db)['Water']
    page = client.get('/app/bills')
    token = page.text.split(f'action="/app/bills/{water.id}/pay')[1].split('name="request_token" value="')[1].split('"')[0]
    data = {'amount_paid': '118.50', 'paid_date': date.today().isoformat(), 'actual_due_date': water.next_due_date.date().isoformat(),
            'note': 'Paid online', 'request_token': token}
    for _ in range(2):
        response = client.post(f'/app/bills/{water.id}/pay{Q}', headers=HX, data=data)
        assert response.status_code == 200 and 'Payment recorded for Water.' in response.text
    db.expire_all()
    payments = db.query(BillPayment).filter_by(bill_id=water.id).all()
    assert len(payments) == 1 and payments[0].amount_paid == 118.5
    assert db.query(JournalEntry).filter_by(action='Paid Bill', entity_id=water.id).count() == 1
    assert [b.name for b in client.get('/app/bills').context['overdue']] == []
    bad = client.post(f'/app/bills/{water.id}/pay{Q}', headers=HX, data={**data, 'amount_paid': 'lots', 'request_token': 'other'})
    assert bad.status_code == 400 and bad.headers['HX-Retarget'] == '#page-feedback'
    assert client.post(f'/app/bills/9999/pay{Q}', headers=HX, data=data).status_code == 404


def test_confirm_invoice_validation_and_duplicate_warning(prototype):
    db, client, ids = prototype
    create(client, 'Power', 20)
    power = bills(db)['Power']
    due = (date.today() + timedelta(days=12)).isoformat()
    assert client.post(f'/app/bills/{power.id}/confirm-invoice{Q}', headers=HX, data={'request_token': 'a'}).status_code == 400
    response = client.post(f'/app/bills/{power.id}/confirm-invoice{Q}', headers=HX,
                           data={'actual_due_date': due, 'actual_amount': '210.40', 'request_token': 'b'})
    assert response.status_code == 200 and 'Confirmed the bill details for Power.' in response.text
    power = bills(db)['Power']
    assert power.is_due_date_confirmed and power.next_due_date.date().isoformat() == due and power.open_invoice.amount_cents == 21040
    duplicate = client.post(f'/app/bills/{power.id}/confirm-invoice{Q}', headers=HX,
                            data={'actual_due_date': due, 'actual_amount': '210.40', 'request_token': 'c'})
    assert duplicate.status_code == 409 and 'Record as a separate invoice' in duplicate.text
    assert duplicate.headers['HX-Retarget'] == '#page-feedback'
    separate = client.post(f'/app/bills/{power.id}/confirm-invoice{Q}', headers=HX,
                           data={'actual_due_date': due, 'actual_amount': '210.40', 'request_token': 'd', 'allow_duplicate': 'true'})
    assert separate.status_code == 200
    assert len(bills(db)['Power'].ordered_invoices) == 2


def test_deactivate_and_reactivate_are_idempotent(prototype):
    db, client, ids = prototype
    create(client, 'Gym', 4)
    gym = bills(db)['Gym']
    for _ in range(2):
        response = client.post(f'/app/bills/{gym.id}/active{Q}', headers=HX, data={})
        assert response.status_code == 200 and 'Deactivated Gym.' in response.text
    assert not bills(db)['Gym'].is_active
    assert db.query(JournalEntry).filter_by(action='Deactivated Bill').count() == 1
    response = client.post(f'/app/bills/{gym.id}/active?view=bills&show_inactive=1', headers=HX, data={'active': 'true'})
    assert 'Reactivated Gym.' in response.text and 'Hide inactive' in response.text
    assert bills(db)['Gym'].is_active
    redirect = client.post(f'/app/bills/{gym.id}/active?view=bills&show_inactive=1', data={'active': 'true'}, follow_redirects=False)
    assert redirect.headers['location'] == '/app/bills?show_inactive=1'


def test_browser_bill_payment(prototype_server, page):
    origin, (db, client, ids) = prototype_server
    create(client, 'Water', -2, amount='80.00')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app')
    page.locator('summary[aria-label="More navigation"]').click()
    page.get_by_role('link', name='Bills').click()
    expect(page.get_by_role('heading', name='Overdue 1')).to_be_visible()
    page.get_by_role('button', name='Record payment').click()
    expect(page.get_by_role('textbox', name='Amount paid for Water')).to_have_value('80.00')
    page.get_by_role('textbox', name='Amount paid for Water').fill('79.95')
    page.locator('#pay-' + str(bills(db)['Water'].id)).get_by_role('button', name='Record payment').click()
    expect(page.locator('#page-feedback')).to_contain_text('Payment recorded for Water.')
    expect(page.get_by_role('heading', name='Paid in the last 30 days 1')).to_be_visible()
    expect(page.locator('.bill-payment-row')).to_contain_text('$79.95')
    assert not errors
