import pytest
from playwright.sync_api import Page, expect
from sqlalchemy.orm import Session
from models import List, Todo
from datetime import datetime
import time

def test_smart_highlight_shows_completed_todo(page: Page, db_session: Session, test_user: str, live_url: str):
    # 1. Setup Data
    lst = List(name="Highlight Test List", show_completed=False)
    db_session.add(lst)
    db_session.flush()

    t1 = Todo(list_id=lst.id, title="Visible Pending Todo")
    t2 = Todo(list_id=lst.id, title="Hidden Completed Todo", completed=True)
    db_session.add_all([t1, t2])
    db_session.commit()

    base_url = live_url

    # 2. Verify Baseline (Without Highlight)
    # Go to list, t2 should be hidden
    page.goto(f"{base_url}/list/{lst.id}")
    expect(page.locator(f"text={t1.title}")).to_be_visible()
    expect(page.locator(f"text={t2.title}")).not_to_be_visible()
    
    # Check checkbox state
    checkbox = page.locator("input[type='checkbox'][onchange^='toggleShowCompleted']")
    expect(checkbox).not_to_be_checked()

    # 3. Verify With Highlight
    # Go to list with highlight param
    page.goto(f"{base_url}/list/{lst.id}?highlight={t2.id}")
    
    # t2 should now be visible logic-wise (rendered in DOM)
    expect(page.locator(f"text={t2.title}")).to_be_visible()
    
    # Checkbox should STILL be unchecked (because DB setting didn't change)
    expect(checkbox).not_to_be_checked()
    
    # 4. Verify Scroll (Indirectly)
    # We can check if the element has the ID
    row = page.locator(f"#todo-row-{t2.id}")
    expect(row).to_be_visible()
    
    # Optional: Execute JS to check if element was scrolled? 
    # Hard to verify "scroll happened" without tracking events, but visibility is the main functional req.
