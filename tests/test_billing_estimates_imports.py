"""Validate recurring estimates and roll back every failed import."""
import copy
import json
from urllib.parse import parse_qs, urlparse

import pytest

from billing_service import record_invoice
from import_logic import import_data_from_json
from models import Bill, BillPayment
from test_billing_accounts import account, day, post


BAD_ESTIMATES = ['inf', '-inf', 'NaN', '1.001', '-1', 'not-money',
                 '92233720368547758.08', '90071992547409.93']


@pytest.mark.parametrize('value', BAD_ESTIMATES)
@pytest.mark.parametrize('confirmed', [False, True])
def test_invalid_create_estimate_cannot_create_bill_invoice_or_journal(account, value, confirmed):
    db, bill, client = account
    before = client.get('/api/export').json()['data']
    response = client.post('/bills/create', data=dict(name='Must not create', expected_amount=value,
                           next_due_date='2026-10-26', ledger_enabled='true',
                           is_due_date_confirmed=str(confirmed).lower()), follow_redirects=False)
    assert response.status_code == 400
    assert client.get('/api/export').json()['data'] == before
    assert client.get('/classic/bills').status_code == client.get('/expenses').status_code == 200


@pytest.mark.parametrize('value', BAD_ESTIMATES)
def test_invalid_edit_estimate_preserves_fields_and_cannot_enable_ledger(account, value):
    db, bill, client = account
    bill.expected_amount = 150
    db.add(BillPayment(bill=bill, amount_paid=42, due_date=day('2026-09-26'), paid_date=day('2026-09-20')))
    db.commit()
    before = client.get('/api/export').json()['data']
    response = client.post(f'/bills/{bill.id}/update', data=dict(
        name='Must not rename', expected_amount=value, next_due_date='2027-01-26',
        note='Must not change', frequency='monthly', ledger_enabled='true'), follow_redirects=False)
    assert response.status_code == 400
    assert client.get('/api/export').json()['data'] == before
    assert not bill.ledger_enabled and not bill.invoices


@pytest.mark.parametrize('value,expected', [('', None), ('   ', None), ('0', 0), ('12.30', 12.3),
                                          (' $1,234.56 ', 1234.56)])
def test_valid_estimate_forms_keep_blank_zero_and_formatted_amounts(account, value, expected):
    db, bill, client = account
    post(client, '/bills/create', dict(name='Valid estimate', expected_amount=value))
    created = db.query(Bill).filter_by(name='Valid estimate').one()
    assert created.expected_amount == expected and not created.invoices
    post(client, f'/bills/{bill.id}/update', dict(name=bill.name, expected_amount=value, next_due_date='2026-10-26'))
    assert bill.expected_amount == expected
    assert client.get('/api/export').status_code == 200
    assert client.get('/classic/bills').status_code == client.get('/expenses').status_code == 200


@pytest.mark.parametrize('value', BAD_ESTIMATES + [float('inf'), float('nan'), -5, 1.001, True, {}, []])
@pytest.mark.parametrize('new_bill', [False, True])
def test_invalid_import_estimate_stops_before_any_write(account, value, new_bill):
    db, bill, client = account
    before = client.get('/api/export').json()['data']
    item = dict(id=99 if new_bill else bill.id, name='Must not create or rename', expected_amount=value)
    payload = {'data': {'folders': [dict(id=99, name='Must not save')], 'bills': [item]}}
    with pytest.raises(ValueError, match='estimate.*No changes were saved'):
        import_data_from_json(db, copy.deepcopy(payload))
    # The import function rolls itself back, including when used without HTTP.
    assert client.get('/api/export').json()['data'] == before
    response = client.post('/api/import', files={'file': ('bad-estimate.json', json.dumps(payload), 'application/json')},
                           follow_redirects=False)
    assert 'import_error=' in response.headers['location'] and 'import_success=' not in response.headers['location']
    assert client.get('/api/export').json()['data'] == before


@pytest.mark.parametrize('value,expected', [(None, None), ('', None), ('  ', None), (0, 0),
                                          (12.3, 12.3), (' $1,234.56 ', 1234.56)])
def test_valid_import_estimates_roundtrip_and_omitted_estimate_is_preserved(account, value, expected):
    db, bill, client = account
    assert import_data_from_json(db, {'data': {'bills': [dict(id=bill.id, expected_amount=value)]}})['errors'] == 0
    assert bill.expected_amount == expected
    payload = client.get('/api/export').json()
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(payload))['errors'] == 0
        assert bill.expected_amount == expected
    assert import_data_from_json(db, {'data': {'bills': [dict(id=bill.id, name='Only rename')]}})['errors'] == 0
    assert bill.expected_amount == expected


@pytest.mark.parametrize('failure', ['new_bill', 'partial_bill', 'late_expense', 'metadata', 'database_error'])
def test_record_failure_rolls_back_earlier_rows_and_reports_the_failed_collection(account, failure):
    db, bill, client = account
    inv = record_invoice(db, bill, '100', day('2026-10-26'), invoice_date=day('2026-10-07'))
    db.commit()
    # First visiting Customization initializes its default preferences.
    assert client.get('/customization').status_code == 200
    before = client.get('/api/export').json()['data']
    data = {'folders': [dict(id=99, name='Must not survive')],
            'bills': [dict(id=bill.id, name='Must not rename')],
            'bill_invoices': [dict(id=inv.id, amount_cents=12000)]}
    if failure == 'new_bill':
        data['bills'].append(dict(id=99, name='Invalid new bill', unsupported_field=True))
        collection = 'bills'
    elif failure == 'partial_bill':
        data['bills'][0]['budget_amount'] = 5  # Read-only property, after the name change.
        collection = 'bills'
    elif failure == 'late_expense':
        data['expenses'] = [dict(id=99, amount=5, date='2026-10-01', unsupported_field=True)]
        collection = 'expenses'
    elif failure == 'metadata':
        data['bill_due_date_submissions'] = [dict(id=99, bill_id=bill.id, request_token='x' * 65)]
        collection = 'bill_due_date_submissions'
    else:
        data['lists'] = [dict(id=99, name='Invalid linked list', folder_id=999)]
        collection = 'lists'
    payload = {'data': data}
    with pytest.raises(ValueError, match=f'Import failed for {collection}.*No changes were saved'):
        import_data_from_json(db, copy.deepcopy(payload))
    assert client.get('/api/export').json()['data'] == before
    response = client.post('/api/import', files={'file': ('bad-record.json', json.dumps(payload), 'application/json')},
                           follow_redirects=False)
    query = parse_qs(urlparse(response.headers['location']).query)
    assert response.status_code == 303 and 'import_success' not in query
    assert collection in query['import_error'][0] and 'No changes were saved' in query['import_error'][0]
    page = client.get(response.headers['location'])
    assert 'Import successful!' not in page.text and 'No changes were saved' in page.text
    assert client.get('/api/export').json()['data'] == before
    # A rollback leaves the same session ready for a corrected import.
    assert import_data_from_json(db, {'data': {'bills': [dict(id=bill.id, name='Corrected')]}})['errors'] == 0
    assert bill.name == 'Corrected' and inv.amount_cents == 10000


@pytest.mark.parametrize('payload', [None, [], {}, {'data': None}, {'data': []}])
def test_invalid_import_structure_cannot_be_reported_as_success(account, payload):
    db, bill, client = account
    before = client.get('/api/export').json()['data']
    with pytest.raises(ValueError, match='data object.*No changes were saved'):
        import_data_from_json(db, payload)
    assert client.get('/api/export').json()['data'] == before


@pytest.mark.parametrize('content', [b'{invalid', b'\xff', b''])
def test_unreadable_upload_reports_no_changes_and_leaves_records_untouched(account, content):
    db, bill, client = account
    client.get('/customization')
    before = client.get('/api/export').json()['data']
    response = client.post('/api/import', files={'file': ('invalid.json', content, 'application/json')},
                           follow_redirects=False)
    query = parse_qs(urlparse(response.headers['location']).query)
    assert 'import_success' not in query and 'No changes were saved' in query['import_error'][0]
    assert client.get('/api/export').json()['data'] == before


def test_valid_import_saves_every_record_and_reports_accurate_counts(account):
    db, bill, client = account
    payload = {'data': {'folders': [dict(id=99, name='Valid folder')],
                        'bills': [dict(id=bill.id, name='Updated utility', expected_amount=125.5),
                                  dict(id=99, name='Valid new bill', expected_amount=0)]}}
    response = client.post('/api/import', files={'file': ('valid.json', json.dumps(payload), 'application/json')},
                           follow_redirects=False)
    query = parse_qs(urlparse(response.headers['location']).query)
    assert query == dict(import_success=['1'], created=['2'], updated=['1'])
    assert bill.name == 'Updated utility' and bill.expected_amount == 125.5
    assert db.get(Bill, 99).expected_amount == 0
    exported = client.get('/api/export').json()['data']
    assert any(row['id'] == 99 for row in exported['folders'])
    assert 'Import successful!' in client.get(response.headers['location']).text
