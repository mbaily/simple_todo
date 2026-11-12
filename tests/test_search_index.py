"""FTS5 search index: matching, trigger upkeep and the LIKE fallback."""
from datetime import datetime

from sqlalchemy import text

import search_index
from main import find_search_results
from models import CalendarEvent, Hashtag, List, Todo
from search_index import index_installed, install_search_index, search_terms


def seed(db):
    shopping = List(name='Shopping')
    chores = List(name='Chores', hashtags=[Hashtag(name='home')])
    db.add_all([shopping, chores])
    db.flush()
    milk = Todo(title='Buy some oatmilk', note='From the corner shop', list_id=shopping.id)
    cafe = Todo(title='Book CAFÉ table', list_id=chores.id)
    sushi = Todo(title='寿司を食べる', list_id=chores.id)
    db.add_all([milk, cafe, sushi, CalendarEvent(title='Dentist visit', note='Bring x-rays', start_at=datetime(2030, 1, 2))])
    db.commit()
    return {'shopping': shopping.id, 'chores': chores.id, 'milk': milk.id, 'cafe': cafe.id, 'sushi': sushi.id}


def found(db, q):
    results = find_search_results(db, q)
    return {key: {item.id for item in items} for key, items in results.items()}


def test_search_terms():
    assert search_terms('  buy  "oat milk" x ') == ['buy', 'oat milk', 'x']
    assert search_terms('""  ') == [] and search_terms(None) == []
    assert search_terms('say "hi') == ['say', 'hi']


def test_index_matches_words_substrings_and_phrases(db_session):
    db = db_session
    ids = seed(db)
    assert index_installed(db)
    # Every term must match, in any order, anywhere inside a word.
    assert found(db, 'milk buy')['todos'] == {ids['milk']}
    assert found(db, 'buy milk dentist')['todos'] == set()
    assert found(db, 'corner')['todos'] == {ids['milk']}
    assert found(db, '"buy some"')['todos'] == {ids['milk']}
    assert found(db, '"buy oatmilk"')['todos'] == set()
    # Terms may match through the list name or a hashtag.
    assert found(db, 'shopping oat')['todos'] == {ids['milk']}
    assert found(db, 'home')['lists'] == {ids['chores']}
    assert found(db, 'shop')['lists'] == {ids['shopping']}
    assert len(found(db, 'x-rays dentist')['events']) == 1
    # Non-ASCII case folding and scripts without word spacing.
    assert found(db, 'café')['todos'] == {ids['cafe']}
    assert found(db, '寿司を')['todos'] == {ids['sushi']}
    # Short terms fall back to LIKE; query syntax is plain text.
    assert found(db, 'oa')['todos'] == {ids['milk']}
    assert found(db, 'buy AND OR NOT * ( ^')['todos'] == set()
    assert found(db, "o'atmilk")['todos'] == set()
    assert found(db, '   ') == {'lists': set(), 'todos': set(), 'events': set()}


def test_triggers_follow_edits_and_deletes(db_session):
    db = db_session
    ids = seed(db)
    todo = db.get(Todo, ids['milk'])
    todo.title = 'Buy almond drink'
    db.commit()
    assert found(db, 'oatmilk')['todos'] == set()
    assert found(db, 'almond')['todos'] == {ids['milk']}
    db.execute(text("UPDATE todos SET note = 'raw sql walnut' WHERE id = :id"), {'id': ids['cafe']})
    db.commit()
    assert found(db, 'walnut')['todos'] == {ids['cafe']}
    db.delete(todo)
    db.commit()
    assert found(db, 'almond')['todos'] == set()
    assert db.execute(text("INSERT INTO todos_fts(todos_fts) VALUES ('integrity-check')")) is not None
    db.commit()
    # Reinstalling is harmless and keeps the index.
    assert install_search_index(db.get_bind())
    assert found(db, 'walnut')['todos'] == {ids['cafe']}


def test_like_fallback_without_index(db_session, monkeypatch):
    db = db_session
    ids = seed(db)
    monkeypatch.setattr('search_index.index_installed', lambda db: False)
    assert found(db, 'milk buy')['todos'] == {ids['milk']}
    assert found(db, 'shopping oat')['todos'] == {ids['milk']}
    assert found(db, 'home')['lists'] == {ids['chores']}
    assert found(db, 'dentist')['events']


def test_existing_rows_are_indexed_on_first_install(db_session):
    db = db_session
    ids = seed(db)
    for name in search_index.INDEXED:
        for suffix in ('insert', 'delete', 'update'):
            db.execute(text(f"DROP TRIGGER {name}_fts_{suffix}"))
        db.execute(text(f"DROP TABLE {name}_fts"))
    db.commit()
    assert not index_installed(db)
    assert found(db, 'oatmilk')['todos'] == {ids['milk']}
    db.commit()
    assert install_search_index(db.get_bind())
    assert index_installed(db)
    assert found(db, 'oatmilk')['todos'] == {ids['milk']}
    assert db.execute(text("SELECT count(*) FROM todos_fts")).scalar() == 3
