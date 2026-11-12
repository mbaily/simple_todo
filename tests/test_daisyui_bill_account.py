"""Bill editor and account history in the daisyUI frontend."""
from datetime import date, timedelta

from playwright.sync_api import expect

from daisyui_test_bills import call_with_form
from models import Bill, BillAdjustment, BillInvoice, BillPayment, BillStatement, JournalEntry
from test_daisyui_bills import bills, create
from test_daisyui_test import HX, prototype, prototype_server

TODAY = date.today().isoformat()


def bill_form(**changes):
    data = {'name': 'Water', 'payee': 'City Water', 'expected_amount': '120.50', 'currency': 'AUD', 'amount_strategy': 'fixed',
            'next_due_date': (date.today() + timedelta(days=9)).isoformat(), 'frequency': 'quarterly', 'frequency_days': '',
            'anchor_day': '', 'category': 'Utilities', 'color': '#3b82f6', 'is_auto_pay': 'true', 'note': 'Meter 42'}
    data.update(changes)
    return {key: value for key, value in data.items() if value is not None}


def op(client, bill, name, **data):
    return client.post(f'/app/bills/{bill.id}/do/{name}?view=bill&bill_id={bill.id}', headers=HX, data=data)


def test_call_with_form_applies_declared_defaults_and_types():
    def route(bill_id: int, amount: str = __import__('fastapi').Form(''), when: str = __import__('fastapi').Form(...),
              flag: bool = __import__('fastapi').Form(False), ref: int | None = __import__('fastapi').Form(None)):
        return dict(bill_id=bill_id, amount=amount, when=when, flag=flag, ref=ref)
    assert call_with_form(route, {'when': 'x', 'flag': 'on', 'ref': '7'}, bill_id=1) == dict(bill_id=1, amount='', when='x', flag=True, ref=7)
    assert call_with_form(route, {'when': 'x', 'ref': ''}, bill_id=1)['ref'] is None
    for form in ({}, {'when': 'x', 'ref': 'seven'}):
        try:
            call_with_form(route, form, bill_id=1)
        except Exception as exc:
            assert getattr(exc, 'status_code', None) == 400
        else:
            raise AssertionError(form)


def test_create_matches_original_and_edit_round_trip(prototype):
    db, client, ids = prototype
    page = client.get('/app/bills/new')
    assert page.status_code == 200 and 'Create bill' in page.text
    response = client.post('/app/bills/new?view=bills', headers=HX, data=bill_form())
    assert response.status_code == 200 and 'Created Water.' in response.text
    water = bills(db)['Water']
    assert response.headers['HX-Push-Url'] == f'/app/bills/{water.id}'
    assert client.post('/bills/create', data=bill_form(name='Original water'), follow_redirects=False).status_code == 303
    original = bills(db)['Original water']
    columns = [c.name for c in Bill.__table__.columns if c.name not in {'id', 'name', 'created_at', 'modified_at'}]
    assert {c: getattr(water, c) for c in columns} == {c: getattr(original, c) for c in columns}
    assert client.post('/app/bills/new?view=bills', headers=HX, data=bill_form(name=' ')).status_code == 400

    edit = client.get(f'/app/bills/{water.id}/edit')
    assert edit.status_code == 200 and 'value="City Water"' in edit.text
    url = f'/app/bills/{water.id}/edit?view=bills'
    saved = client.post(url, headers=HX, data=bill_form(name='Water and sewer', frequency='custom', frequency_days='45', color=''))
    assert saved.status_code == 200 and 'Saved Water and sewer.' in saved.text
    water = bills(db)['Water and sewer']
    assert water.frequency == 'custom' and water.frequency_days == 45 and water.color is None
    bad = client.post(url, headers=HX, data=bill_form(expected_amount='12.345'))
    assert bad.status_code == 400 and bad.headers['HX-Retarget'] == '#page-feedback'
    assert bills(db)['Water and sewer'].expected_amount == 120.5
    assert client.get('/app/bills/9999/edit').status_code == 404


def test_history_matches_original_and_operations_round_trip(prototype):
    db, client, ids = prototype
    create(client, 'Power', 5, amount='200.00', ledger_enabled='true')
    power = bills(db)['Power']
    due = (date.today() + timedelta(days=5)).isoformat()
    assert 'Invoice saved.' in op(client, power, 'invoice', amount='210.00', due_date=due, invoice_number='INV-1',
                                  request_token='t1').text
    invoice = db.query(BillInvoice).filter_by(bill_id=power.id).one()
    assert invoice.amount_cents == 21000 and invoice.invoice_number == 'INV-1'
    assert op(client, power, 'invoice', amount='210.00', due_date=due, request_token='t2').status_code == 409
    assert 'Payment recorded.' in op(client, power, 'pay', invoice_id=str(invoice.id), amount_paid='200.00', paid_date=TODAY,
                                     request_token='t3').text
    assert 'Extra payment recorded.' in op(client, power, 'extra-payment', amount_paid='25.00', paid_date=TODAY, note='Top up',
                                           request_token='t4').text
    assert op(client, power, 'extra-payment', amount_paid='0', paid_date=TODAY, request_token='t5').status_code == 400
    assert 'Adjustment saved.' in op(client, power, 'adjustment', kind='supplier_credit', amount='5.00', effective_date=TODAY,
                                     request_token='t6').text
    adjustment = db.query(BillAdjustment).filter_by(bill_id=power.id).one()
    assert 'Supplier balance recorded.' in op(client, power, 'statement', amount='0.00', balance_type='owing',
                                              as_of_date=TODAY, request_token='t7').text
    statement = db.query(BillStatement).filter_by(bill_id=power.id).one()

    page = client.get(f'/app/bills/{power.id}')
    original = client.get(f'/bills/{power.id}/history')
    assert page.status_code == 200
    assert [p.id for p in page.context['payments']] == [p.id for p in original.context['payments']]
    assert page.context['total_paid'] == original.context['total_paid'] == 225.0
    assert [row['status'] for row in page.context['statement_comparisons']] == [row['status'] for row in original.context['statement_comparisons']]
    assert 'INV-1' in page.text and 'Top up' in page.text

    payment = db.query(BillPayment).filter_by(bill_id=power.id, invoice_id=invoice.id).one()
    assert 'Note added.' in op(client, power, 'payment-note', payment_id=str(payment.id), note_text='Receipt filed').text
    assert 'Payment updated.' in op(client, power, 'payment-edit', payment_id=str(payment.id), paid_date=TODAY,
                                    amount_paid='199.00', request_token='t8').text
    db.expire_all()
    assert db.get(BillPayment, payment.id).amount_paid == 199.0 and 'Receipt filed' in db.get(BillPayment, payment.id).note
    assert 'Adjustment deleted.' in op(client, power, 'adjustment-delete', adjustment_id=str(adjustment.id)).text
    assert 'Supplier balance deleted.' in op(client, power, 'statement-delete', statement_id=str(statement.id)).text
    payment_id, adjustment_id = payment.id, adjustment.id
    assert 'Payment deleted.' in op(client, power, 'payment-delete', payment_id=str(payment_id)).text
    db.expire_all()
    assert db.query(BillPayment).filter_by(id=payment_id).count() == 0
    assert db.query(BillAdjustment).filter_by(id=adjustment_id).count() == 0
    assert op(client, power, 'teleport').status_code == 404


def test_operations_reject_records_of_another_bill_and_late_override_is_idempotent(prototype):
    db, client, ids = prototype
    create(client, 'Gas', -10, amount='50.00')
    create(client, 'Rates', 20, amount='400.00')
    gas, rates = bills(db)['Gas'], bills(db)['Rates']
    client.post(f'/bills/{gas.id}/pay', data={'amount_paid': '50.00', 'paid_date': TODAY,
                                              'actual_due_date': gas.next_due_date.date().isoformat(), 'request_token': 'p'})
    payment = db.query(BillPayment).filter_by(bill_id=gas.id).one()
    assert op(client, rates, 'payment-delete', payment_id=str(payment.id)).status_code == 404
    url = f'/app/bills/{gas.id}/late-override?view=bill&bill_id={gas.id}'
    for _ in range(2):
        assert 'Marked as on time.' in client.post(url, headers=HX, data={'payment_id': payment.id, 'overridden': 'true'}).text
    db.expire_all()
    assert db.get(BillPayment, payment.id).is_late_overridden
    client.post(url, headers=HX, data={'payment_id': payment.id})
    db.expire_all()
    assert not db.get(BillPayment, payment.id).is_late_overridden
    assert client.post(f'/app/bills/{rates.id}/late-override', headers=HX, data={'payment_id': payment.id}).status_code == 404

    deleted = client.post(f'/app/bills/{rates.id}/delete?view=bills', headers=HX)
    assert deleted.status_code == 200 and 'Deleted Rates and its history.' in deleted.text
    assert deleted.headers['HX-Push-Url'] == '/app/bills' and 'Rates' not in bills(db)


def test_browser_bill_account(prototype_server, page):
    origin, (db, client, ids) = prototype_server
    create(client, 'Power', 5, amount='200.00')
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto(origin + '/app/bills')
    page.get_by_role('link', name='Power').click()
    expect(page.get_by_role('heading', name='Power', exact=True)).to_be_visible()
    page.get_by_role('button', name='Record invoice').click()
    page.get_by_role('textbox', name='New invoice amount').fill('205.00')
    page.get_by_role('combobox', name='Amount shown is').first.select_option('net')
    expect(page.locator('#new-invoice .invoice-credit')).to_be_visible()
    page.get_by_role('combobox', name='Amount shown is').first.select_option('gross')
    page.get_by_role('button', name='Record invoice and apply credit').click()
    expect(page.locator('#page-feedback')).to_contain_text('Invoice saved.')
    expect(page.locator('#billing')).to_contain_text('$205.00')
    page.get_by_role('link', name='Edit bill').click()
    page.get_by_role('textbox', name='Name').fill('Electricity')
    page.get_by_role('combobox', name='Frequency').select_option('custom')
    expect(page.get_by_role('spinbutton', name='Days between bills')).to_be_visible()
    page.get_by_role('spinbutton', name='Days between bills').fill('30')
    page.get_by_role('button', name='Save changes').click()
    expect(page.locator('#page-feedback')).to_contain_text('Saved Electricity.')
    expect(page.get_by_role('heading', name='Electricity', exact=True)).to_be_visible()
    assert not errors
