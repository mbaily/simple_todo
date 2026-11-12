import pytest
from playwright.sync_api import Page, expect
from sqlalchemy.orm import Session
from models import List, Todo
from datetime import datetime, timedelta
import time

def test_empty_list_state_message(page: Page, db_session: Session, test_user: str, live_url: str):
    # 1. Setup Data
    lst = List(name="Filtered List", show_completed=False, show_delayed=False)
    db_session.add(lst)
    db_session.flush()

    # Create a delayed todo (hidden by default)
    t1 = Todo(list_id=lst.id, title="Future Task", delayed_until=datetime.now() + timedelta(days=1))
    db_session.add(t1)
    db_session.commit()

    base_url = live_url

    # 2. Visit List
    page.goto(f"{base_url}/list/{lst.id}")
    
    # 3. Verify Message
    # Should NOT see "No todos yet"
    expect(page.locator("text=No todos yet")).not_to_be_visible()
    
    # Should see "No visible todos..."
    expect(page.locator("text=No visible todos")).to_be_visible()
    
    # 4. Verify Baseline (Empty List)
    lst_empty = List(name="Empty List")
    db_session.add(lst_empty)
    db_session.commit()
    
    page.goto(f"{base_url}/list/{lst_empty.id}")
    expect(page.locator("text=No todos yet")).to_be_visible()
