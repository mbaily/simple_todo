import pytest
from playwright.sync_api import Page, expect
from sqlalchemy.orm import Session
from models import List, Todo
from datetime import datetime
import time

def test_empty_list_state_refined_completed(page: Page, db_session: Session, test_user: str, live_url: str):
    # 1. Setup Data - List with Show Completed = False
    lst = List(name="Completed Items Hidden", show_completed=False)
    db_session.add(lst)
    db_session.flush()

    # Create ONLY a completed todo
    t1 = Todo(list_id=lst.id, title="Done Task", completed=True)
    db_session.add(t1)
    db_session.commit()

    base_url = live_url

    # 2. Visit List
    page.goto(f"{base_url}/list/{lst.id}")
    
    # 3. Verify Message
    # Should NOT see "No todos yet"
    expect(page.locator("text=No todos yet")).not_to_be_visible()
    
    # Should see "No visible todos..." (because total_count=1, visible=0)
    expect(page.locator("text=No visible todos")).to_be_visible()
    
    # 4. Toggle Show Completed to confirm item exists
    # The label has title="Show/Hide Completed", click that
    page.click("label[title='Show/Hide Completed']")
    
    # 5. Verify it is now visible
    # Reload happens on toggle usually, or JS update. 
    # In this app, toggle reload page? Let's check view_list.html script.
    # It calls toggleShowCompleted -> fetch -> reload.
    
    page.wait_for_load_state("networkidle")
    expect(page.locator(f"text={t1.title}")).to_be_visible()
