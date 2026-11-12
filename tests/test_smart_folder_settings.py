"""Canonical inheritance, legacy conversion, and editable smart-folder presets."""
import copy
import json

import pytest
from playwright.sync_api import expect
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

import database
from entity_history import install_entity_history
from import_logic import import_data_from_json
from models import Base, Hashtag, JournalEntry, List, Note, SmartFolder, SmartFolderIncludeHashtag, SmartFolderExcludeHashtag, Todo
from serializers import serialize_smart_folder
from sync_apply import apply_smart_folder
from test_daisyui_test import HX, prototype, prototype_server


def legacy_database(path):
    engine = create_engine(f'sqlite:///{path}')
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE smart_folder_include_hashtags ADD COLUMN include_subtodos BOOLEAN DEFAULT 0'))
        connection.execute(text('ALTER TABLE smart_folder_include_hashtags ADD COLUMN include_list_items BOOLEAN DEFAULT 0'))
        connection.execute(text('ALTER TABLE smart_folder_exclude_hashtags ADD COLUMN include_subtodos BOOLEAN DEFAULT 0'))
    with Session(engine) as db:
        tag, skip = Hashtag(name='Work'), Hashtag(name='Skip')
        first = SmartFolder(name='Legacy include', include_hashtags_assoc=[SmartFolderIncludeHashtag(hashtag=tag, metadata_json={'keep': True})])
        second = SmartFolder(name='Legacy exclusion', exclude_hashtags_assoc=[SmartFolderExcludeHashtag(hashtag=skip)])
        third = SmartFolder(name='Canonical', include_list_items=True)
        db.add_all([first, second, third])
        db.commit()
    with engine.begin() as connection:
        connection.execute(text('UPDATE smart_folder_include_hashtags SET include_subtodos = 1, include_list_items = 1'))
        connection.execute(text('UPDATE smart_folder_exclude_hashtags SET include_subtodos = 1'))
    install_entity_history(engine)
    return engine


def test_legacy_database_migrates_once_preserves_metadata_and_history(tmp_path):
    engine = legacy_database(tmp_path / 'legacy.db')
    try:
        database.run_migrations(engine)
        for table in ('smart_folder_include_hashtags', 'smart_folder_exclude_hashtags'):
            columns = {column['name'] for column in inspect(engine).get_columns(table)}
            assert 'include_subtodos' not in columns and 'include_list_items' not in columns
        with Session(engine) as db:
            folders = {folder.name: folder for folder in db.query(SmartFolder)}
            assert (folders['Legacy include'].include_subtodos, folders['Legacy include'].include_list_items) == (True, True)
            assert (folders['Legacy exclusion'].include_subtodos, folders['Legacy exclusion'].include_list_items) == (True, False)
            assert folders['Canonical'].include_list_items
            assoc = folders['Legacy include'].include_hashtags_assoc[0]
            assert assoc.metadata_json == {'keep': True}
            folders['Legacy include'].include_subtodos = False
            folders['Legacy include'].include_list_items = False
            assoc.metadata_json = {'keep': True, 'after_migration': True}
            db.commit()
        database.run_migrations(engine)
        install_entity_history(engine)
        with Session(engine) as db:
            saved = db.query(SmartFolder).filter_by(name='Legacy include').one()
            assert not saved.include_subtodos and not saved.include_list_items
            assert db.query(SmartFolder).count() == 3
            assert db.query(SmartFolderIncludeHashtag).count() == db.query(SmartFolderExcludeHashtag).count() == 1
            snapshots = db.execute(text("SELECT snapshot FROM entity_versions WHERE entity_type = 'smart_folder_include_hashtags' ORDER BY id")).scalars().all()
            assert json.loads(snapshots[0])['include_subtodos'] == 1  # Historical snapshots are retained.
            assert 'include_subtodos' not in json.loads(snapshots[-1])
            assert json.loads(json.loads(snapshots[-1])['metadata_json']) == {'keep': True, 'after_migration': True}
            assert db.execute(text('PRAGMA foreign_key_check')).all() == []
    finally:
        engine.dispose()


def test_inheritance_migration_rolls_back_schema_and_flags_on_failure(tmp_path, monkeypatch):
    engine = legacy_database(tmp_path / 'rollback.db')
    try:
        def fail(*args):
            raise RuntimeError('Cannot rebuild triggers')
        monkeypatch.setattr(database, 'install_table_history_triggers', fail)
        with pytest.raises(RuntimeError, match='Cannot rebuild triggers'):
            database.run_migrations(engine)
        assert 'include_subtodos' in {column['name'] for column in inspect(engine).get_columns('smart_folder_include_hashtags')}
        with Session(engine) as db:
            saved = db.query(SmartFolder).filter_by(name='Legacy include').one()
            assert not saved.include_subtodos and not saved.include_list_items
            saved.include_hashtags_assoc[0].metadata_json = {'history_still_works': True}
            db.commit()
    finally:
        engine.dispose()


def test_old_missing_tag_does_not_break_matching_or_explanations(tmp_path):
    from smart_folder_service import SmartFolderService
    engine = legacy_database(tmp_path / 'missing-tag.db')
    try:
        database.run_migrations(engine)
        # Older installs could delete a tag without deleting its view-only links.
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM hashtags WHERE name = 'Work'"))
        with Session(engine) as db:
            folder = db.query(SmartFolder).filter_by(name='Legacy include').one()
            todos, _ = SmartFolderService.get_todos(db, folder)
            assert todos == []
            assert SmartFolderService.todo_match_reasons(db, folder, todos) == {}
    finally:
        engine.dispose()


@pytest.mark.parametrize('transport', ['import', 'sync'])
def test_old_payloads_convert_but_explicit_false_and_partial_updates_win(prototype, transport):
    db, client, ids = prototype
    folder = SmartFolder(name='Portable')
    db.add(folder)
    db.commit()
    def apply(values):
        payload = {'id': folder.id, **values}
        if transport == 'import':
            import_data_from_json(db, {'data': {'smart_folders': [copy.deepcopy(payload)]}})
        else:
            apply_smart_folder(db, payload, {})
            db.commit()
        db.expire_all()

    legacy = dict(include_hashtags=[dict(id=ids['tag'], include_subtodos=True, include_list_items=True)])
    apply(legacy)
    assert folder.include_subtodos and folder.include_list_items
    apply(dict(name='Renamed'))
    assert folder.include_subtodos and folder.include_list_items
    apply({**legacy, 'include_subtodos': False, 'include_list_items': False})
    assert not folder.include_subtodos and not folder.include_list_items
    exported = serialize_smart_folder(folder)
    assert exported['include_hashtags'] == [dict(id=ids['tag'], name='personal')]
    assert 'include_subtodos' not in exported['include_hashtags_assoc'][0]
    apply(exported | dict(include_subtodos=False, include_list_items=False))
    assert not folder.include_subtodos and not folder.include_list_items
    # Older API aliases and nested hashtag objects are accepted at the boundary.
    apply(dict(include_hashtags_assoc=[dict(hashtag=dict(id=ids['tag']), include_subtodos=True)]))
    assert folder.include_subtodos and not folder.include_list_items
    apply(dict(include_hashtags=[]))
    assert not folder.include_hashtags_assoc and folder.include_subtodos


def test_current_and_legacy_forms_share_folder_switches(prototype):
    db, client, ids = prototype
    first = dict(name='Legacy form', show_todos='true', hashtag_ids=str(ids['tag']),
                 **{f'include_subtodos_{ids["tag"]}': 'on', f'include_list_items_{ids["tag"]}': 'on'})
    assert client.post('/smart-folder/create', data=first, follow_redirects=False).status_code == 303
    saved = db.query(SmartFolder).one()
    assert saved.include_subtodos and saved.include_list_items
    classic = client.get(f'/smart-folder/{saved.id}/edit')
    assert 'name="include_subtodos"' in classic.text
    assert f'name="include_subtodos_{ids["tag"]}"' not in classic.text
    # Explicit off values cannot be overridden by stale nested or old-form flags.
    client.post(f'/smart-folder/{saved.id}/update', data={**first, 'include_subtodos': 'false', 'include_list_items': 'false'})
    db.expire_all()
    assert not saved.include_subtodos and not saved.include_list_items
    daisy = client.get(f'/app/smart-folder/{saved.id}/edit')
    assert not daisy.context['values']['subtodos'] and not daisy.context['values']['list_items']
    data = client.get(f'/api/data/smart-folder/{saved.id}/edit').json()
    assert data['folder']['include_subtodos'] is False and data['folder']['include_list_items'] is False
    assert data['folder_include_subtodos_map'] == {str(ids['tag']): False}
    assert client.get('/api/data/smart-folders').json()['folders'][0]['include_subtodos'] is False


def test_presets_are_editable_drafts_without_creating_records(prototype):
    db, client, ids = prototype
    for preset, expected in [('open-work', ['hash:work']), ('open-tasks', []), ('work-lists', ['hash:work'])]:
        response = client.get('/app/smart-folders/new?preset=' + preset)
        assert response.status_code == 200
        state = response.context['values']
        assert state['include'] == expected and not state['show_events']
        assert state['show_todos'] == (preset != 'work-lists')
        assert state['uncompleted_only'] == (preset != 'work-lists')
    assert client.get('/app/smart-folders/new?preset=unknown').status_code == 404
    assert db.query(SmartFolder).count() == db.query(JournalEntry).count() == 0


def test_preview_reasons_use_the_same_rules_for_text_tags_ancestors_and_lists(prototype):
    db, client, ids = prototype
    root = db.get(Todo, ids['todo'])
    root.note = '#work'
    child = Todo(title='Inherited child', list_id=ids['list'], parent=root)
    grandchild = Todo(title='Inherited grandchild', list_id=ids['list'], parent=child)
    written_note = Todo(title='Written in a note', list_id=ids['list'])
    db.get(List, ids['other']).name += ' #work'
    db.add_all([child, grandchild, written_note, Note(todo=written_note, list_id=ids['list'], text='#work')])
    db.commit()
    form = dict(show_todos='true', include='hash:work', subtodos='true', list_items='true')
    draft = client.post('/app/smart-folders/draft', headers=HX, data=form)
    assert draft.status_code == 200
    reasons = {title: why for title, _, why in draft.context['matches']['todo_names']}
    assert reasons[root.title] == ['Own text: #work']
    assert reasons[child.title] == reasons[grandchild.title] == ['Parent/ancestor: #work']
    assert reasons[written_note.title] == ['Own text: #work']
    assert reasons[db.get(Todo, ids['old']).title] == ['List: #work']
    # Match-all can require a task's own tag and a hashtag inherited from its list.
    db.get(List, ids['list']).name += ' #work'
    db.commit()
    draft = client.post('/app/smart-folders/draft', headers=HX,
                        data={**form, 'include': ['hash:work', f'tag:{ids["tag"]}'], 'match': 'all'})
    reasons = {title: why for title, _, why in draft.context['matches']['todo_names']}
    assert set(reasons[root.title]) == {'Own tag: personal', 'Own text: #work', 'List: #work'}
    assert set(reasons[child.title]) == {'Parent/ancestor: personal', 'Parent/ancestor: #work', 'List: #work'}
    disabled = client.post('/app/smart-folders/draft', headers=HX,
                           data={**form, 'subtodos': 'false', 'list_items': 'false'})
    reasons = {title: why for title, _, why in disabled.context['matches']['todo_names']}
    assert set(reasons) == {root.title, written_note.title}
    assert all(why == ['Own text: #work'] for why in reasons.values())
    assert db.query(SmartFolder).count() == db.query(JournalEntry).count() == 0


def test_browser_preset_can_be_customized_and_explanations_follow_switches(prototype_server, page):
    origin, (db, _, ids) = prototype_server
    root = db.get(Todo, ids['todo'])
    root.note = '#work'
    db.add(Todo(title='Inherited sample', list_id=ids['list'], parent=root))
    db.get(List, ids['other']).name += ' #work'
    db.commit()
    page.goto(origin + '/app/smart-folders/new')
    page.get_by_text('Start with a preset', exact=False).click()
    page.get_by_role('link', name='Open tasks with #work', exact=True).click()
    expect(page.get_by_role('textbox', name='Name', exact=True)).to_have_value('Open tasks with #work')
    preview = page.locator('#smart-preview')
    expect(preview).to_contain_text('Own text: #work')
    expect(preview).not_to_contain_text('Parent/ancestor: #work')
    page.get_by_role('checkbox', name='Also match subtasks of matching tasks', exact=True).check()
    expect(preview).to_contain_text('Inherited sample')
    expect(preview).to_contain_text('Parent/ancestor: #work')
    page.get_by_role('checkbox', name='Also match tasks in matching lists', exact=True).check()
    expect(preview).to_contain_text('List: #work')
    page.screenshot(path='/tmp/smart-folder-explanations-desktop.png', full_page=True)
    page.set_viewport_size({'width': 320, 'height': 844})
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    page.screenshot(path='/tmp/smart-folder-explanations-mobile.png', full_page=True)
    assert db.query(SmartFolder).count() == 0
    page.get_by_role('textbox', name='Name', exact=True).fill('Custom work view')
    page.get_by_role('button', name='Create smart folder', exact=True).click()
    expect(page.get_by_role('heading', name='Custom work view', exact=True)).to_be_visible()
    page.get_by_role('link', name='Edit rules', exact=True).first.click()
    expect(page.get_by_text('Start with a preset', exact=False)).to_have_count(0)
    page.get_by_role('checkbox', name='Also match subtasks of matching tasks', exact=True).uncheck()
    expect(preview).not_to_contain_text('Parent/ancestor: #work')
    page.get_by_role('checkbox', name='Also match tasks in matching lists', exact=True).uncheck()
    expect(preview).not_to_contain_text('List: #work')
    page.get_by_role('button', name='Save changes', exact=True).click()
    expect(page.get_by_role('heading', name='Custom work view', exact=True)).to_be_visible()
    db.expire_all()
    saved = db.query(SmartFolder).one()
    assert saved.include_text_hashtags == ['work'] and saved.uncompleted_only
    assert not saved.include_subtodos and not saved.include_list_items
