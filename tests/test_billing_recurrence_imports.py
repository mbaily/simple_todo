"""Malformed imported schedules must not break billing or expense pages."""
import copy
import json
from urllib.parse import parse_qs, urlparse

import pytest

from import_logic import import_data_from_json
from models import Bill
from test_billing_accounts import account


INVALID = [dict(frequency_days=value) for value in ['not-days', '45', '', None, 0, -1, True, 1.5, {}, [], 10**30]]
INVALID += [dict(frequency=value) for value in ['not-frequency', '', None, True, []]]
INVALID += [dict(anchor_day=value) for value in ['15', 0, 32, True, {}]]


@pytest.mark.parametrize('changes', INVALID)
@pytest.mark.parametrize('new_bill', [False, True])
def test_invalid_schedule_import_rolls_back_all_rows_and_keeps_pages_available(account, changes, new_bill):
    db, bill, client = account
    bill.frequency, bill.frequency_days, bill.anchor_day = 'custom', 45, 26
    bill.expected_amount = 100
    db.commit()
    client.get('/customization')
    before = client.get('/api/export').json()['data']
    record_id = 99 if new_bill else bill.id
    values = dict(id=record_id, name='Must not create or rename', frequency='custom', frequency_days=45,
                  anchor_day=26, next_due_date='2026-10-26')
    values.update(changes)
    payload = {'data': {'folders': [dict(id=99, name='Must not create')], 'bills': [values]}}
    with pytest.raises(ValueError, match=f'Bill #{record_id} recurrence.*No changes were saved'):
        import_data_from_json(db, copy.deepcopy(payload))
    assert client.get('/api/export').json()['data'] == before
    response = client.post('/api/import', files={'file': ('invalid-schedule.json', json.dumps(payload), 'application/json')},
                           follow_redirects=False)
    query = parse_qs(urlparse(response.headers['location']).query)
    assert 'import_success' not in query and f'Bill #{record_id} recurrence' in query['import_error'][0]
    assert 'No changes were saved' in query['import_error'][0]
    assert client.get('/api/export').json()['data'] == before
    assert client.get('/classic/bills').status_code == client.get('/expenses').status_code == 200


@pytest.mark.parametrize('frequency,interval', [('weekly', None), ('fortnightly', None), ('monthly', None),
                                              ('bimonthly', None), ('quarterly', None), ('yearly', None),
                                              ('custom', 1), ('custom', 45)])
def test_valid_schedule_import_and_partial_name_update_preserve_recurrence(account, frequency, interval):
    db, bill, client = account
    payload = {'data': {'bills': [dict(id=bill.id, frequency=frequency, frequency_days=interval, anchor_day=15)]}}
    assert import_data_from_json(db, payload)['errors'] == 0
    assert (bill.frequency, bill.frequency_days, bill.anchor_day) == (frequency, interval, 15)
    assert import_data_from_json(db, {'data': {'bills': [dict(id=bill.id, name='Only name')]}})['errors'] == 0
    assert (bill.frequency, bill.frequency_days, bill.anchor_day) == (frequency, interval, 15)
    exported = client.get('/api/export').json()
    for _ in range(2):
        assert import_data_from_json(db, copy.deepcopy(exported))['errors'] == 0
        assert (bill.frequency, bill.frequency_days, bill.anchor_day) == (frequency, interval, 15)
    assert client.get('/classic/bills').status_code == client.get('/expenses').status_code == 200


def test_new_bill_without_recurrence_defaults_and_explicit_null_anchor_remain_supported(account):
    db, bill, client = account
    assert import_data_from_json(db, {'data': {'bills': [dict(id=99, name='Default recurrence')]}})['created'] == 1
    new = db.get(Bill, 99)
    assert new.frequency == 'monthly' and new.frequency_days is None
    assert import_data_from_json(db, {'data': {'bills': [dict(id=bill.id, anchor_day=None)]}})['errors'] == 0
    assert bill.anchor_day is None


def test_partial_import_can_repair_an_older_malformed_interval(account):
    db, bill, client = account
    bill.frequency, bill.frequency_days, bill.expected_amount = 'custom', 'not-days', 100
    db.commit()
    assert import_data_from_json(db, {'data': {'bills': [dict(id=bill.id, frequency_days=45)]}})['errors'] == 0
    assert bill.frequency_days == 45
    assert client.get('/classic/bills').status_code == client.get('/expenses').status_code == 200


def test_final_merged_schedule_allows_multiple_partial_updates_and_rejects_date_overflow(account):
    db, bill, client = account
    assert import_data_from_json(db, {'data': {'bills': [dict(id=bill.id, frequency='custom'),
                                                      dict(id=bill.id, frequency_days=45)]}})['errors'] == 0
    assert bill.frequency == 'custom' and bill.frequency_days == 45
    before = client.get('/api/export').json()['data']
    with pytest.raises(ValueError, match='Bill #1 recurrence.*supported date range.*No changes were saved'):
        import_data_from_json(db, {'data': {'bills': [dict(id=bill.id, frequency_days=3652058)]}})
    assert client.get('/api/export').json()['data'] == before
