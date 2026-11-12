import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session
from models import (
    Base,
    List,
    Todo,
    Folder,
    Note,
    Hashtag,
    CalendarEvent,
    CalendarEventCompletion,
    CalendarDay,
    EventGroup,
    ListDatetimeHeader,
    SmartFolder,
    SmartFolderIncludeHashtag,
    SmartFolderExcludeHashtag,
    NamedSearch,
    Link,
    JournalEntry,
    DokuWikiConfirmedItem,
    UserSettings,
    IngredientEvent,
    IngredientGroup,
    Category,
    Bill,
    BillDueDateSubmission,
    BillRecordSubmission,
    BillPaymentSubmission,
    BillPayment,
    BillInvoice,
    BillAllocation,
    BillAdjustment,
    BillAdjustmentAllocation,
    BillStatement,
    Expense,
)
import database


def test_all_models_have_metadata_json_attribute():
    models = [
        SmartFolderIncludeHashtag,
        SmartFolderExcludeHashtag,
        List,
        Todo,
        ListDatetimeHeader,
        Hashtag,
        NamedSearch,
        SmartFolder,
        CalendarEvent,
        CalendarEventCompletion,
        CalendarDay,
        EventGroup,
        Link,
        JournalEntry,
        DokuWikiConfirmedItem,
        Folder,
        Note,
        UserSettings,
        IngredientEvent,
        IngredientGroup,
        Category,
        Bill,
        BillDueDateSubmission,
        BillRecordSubmission,
        BillPaymentSubmission,
        BillPayment,
        BillInvoice,
        BillAllocation,
        BillAdjustment,
        BillAdjustmentAllocation,
        BillStatement,
        Expense,
    ]
    for model in models:
        assert hasattr(model, "metadata_json"), f"{model.__name__} is missing metadata_json"
        assert "metadata_json" in [c.name for c in model.__table__.columns], f"{model.__name__} table is missing metadata_json column"


def test_entities_default_empty_dict_on_creation():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lst = List(name="Inbox")
        todo = Todo(title="Buy milk", list=lst)
        note = Note(text="A note", list=lst)
        folder = Folder(name="Work")
        cat = Category(name="Personal")
        exp = Expense(amount=12.50, description="Coffee")

        session.add_all([lst, todo, note, folder, cat, exp])
        session.commit()

        for entity in [lst, todo, note, folder, cat, exp]:
            session.refresh(entity)
            assert entity.metadata_json == {}
            assert isinstance(entity.metadata_json, dict)


def test_entities_store_and_update_metadata():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        lst = List(name="Project", metadata_json={"color": "blue", "tags": ["work", "active"], "count": 42})
        session.add(lst)
        session.commit()
        session.refresh(lst)

        assert lst.metadata_json == {"color": "blue", "tags": ["work", "active"], "count": 42}

        # Update metadata
        lst.metadata_json = {"color": "red", "archived_reason": "done"}
        session.commit()
        session.refresh(lst)

        assert lst.metadata_json == {"color": "red", "archived_reason": "done"}


def test_migration_adds_metadata_json_column_to_existing_tables():
    engine = create_engine("sqlite:///:memory:")
    with engine.connect() as conn:
        conn.exec_driver_sql("CREATE TABLE lists (id INTEGER PRIMARY KEY, name VARCHAR(255) NOT NULL)")
        conn.exec_driver_sql("INSERT INTO lists (name) VALUES ('Pre-migration list')")
        conn.commit()

    database.run_migrations(engine)

    inspector = inspect(engine)
    columns = [col["name"] for col in inspector.get_columns("lists")]
    assert "metadata_json" in columns

    with engine.connect() as conn:
        row = conn.exec_driver_sql("SELECT name, metadata_json FROM lists WHERE id = 1").fetchone()
        assert row[0] == "Pre-migration list"
        assert row[1] == "{}"
