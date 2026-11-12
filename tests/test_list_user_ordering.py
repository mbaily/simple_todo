"""Test user-defined ordering for lists."""
import pytest
from datetime import datetime, timedelta
from models import List
from test_billing_accounts import account


def test_list_user_defined_order_and_reorder_endpoint(account):
    db, _, client = account

    # Create 3 lists with staggered creation times
    now = datetime.now()
    list_a = List(name="List Alpha", created_at=now - timedelta(days=3), modified_at=now - timedelta(days=1), sort_order=0)
    list_b = List(name="List Beta", created_at=now - timedelta(days=2), modified_at=now - timedelta(days=2), sort_order=0)
    list_c = List(name="List Gamma", created_at=now - timedelta(days=1), modified_at=now - timedelta(days=3), sort_order=0)

    db.add_all([list_a, list_b, list_c])
    db.commit()

    # 1. Test GET /lists/reorder page
    res_page = client.get("/lists/reorder")
    assert res_page.status_code == 200
    assert "Reorder Lists" in res_page.text
    assert "List Alpha" in res_page.text
    assert "List Beta" in res_page.text
    assert "List Gamma" in res_page.text
    assert "drag-handle" in res_page.text
    assert "static/htmx.min.js" in res_page.text
    assert "static/alpine.min.js" in res_page.text

    # 2. Test Home Page sort options and link
    res_home = client.get("/classic")
    assert res_home.status_code == 200
    assert "Reorder Lists" in res_home.text
    assert "Sort by:" in res_home.text
    assert "Modified" in res_home.text
    assert "Created" in res_home.text
    assert "User" in res_home.text

    # 3. Test POST /lists/reorder using HTMX form data
    # Set custom order: Gamma (c), Alpha (a), Beta (b)
    new_order = [list_c.id, list_a.id, list_b.id]
    res_save = client.post(
        "/lists/reorder",
        data={"list_ids": [str(list_c.id), str(list_a.id), str(list_b.id)]},
        headers={"HX-Request": "true"}
    )
    assert res_save.status_code == 200
    assert "Saved" in res_save.text

    # Verify sort_order values in DB
    db.expire_all()
    assert db.get(List, list_c.id).sort_order == 0
    assert db.get(List, list_a.id).sort_order == 1
    assert db.get(List, list_b.id).sort_order == 2

    # 4. Test Home page default (no sort query parameter -> defaults to user sort)
    res_default = client.get("/classic")
    assert res_default.status_code == 200
    idx_c = res_default.text.find("List Gamma")
    idx_a = res_default.text.find("List Alpha")
    idx_b = res_default.text.find("List Beta")
    assert idx_c < idx_a < idx_b

    # Test explicit ?sort=user as well
    res_user_sort = client.get("/classic?sort=user")
    assert res_user_sort.status_code == 200
    idx_c2 = res_user_sort.text.find("List Gamma")
    idx_a2 = res_user_sort.text.find("List Alpha")
    idx_b2 = res_user_sort.text.find("List Beta")
    assert idx_c2 < idx_a2 < idx_b2

    # 5. Test JSON submission to /lists/reorder
    # Reverse order: Beta (b), Alpha (a), Gamma (c)
    res_json = client.post(
        "/lists/reorder",
        json={"list_ids": [list_b.id, list_a.id, list_c.id]},
        headers={"HX-Request": "true"}
    )
    assert res_json.status_code == 200

    db.expire_all()
    assert db.get(List, list_b.id).sort_order == 0
    assert db.get(List, list_a.id).sort_order == 1
    assert db.get(List, list_c.id).sort_order == 2

    # Check API home data defaults to user sort
    res_api = client.get("/api/data/home")
    assert res_api.status_code == 200
    api_lists = res_api.json()["lists"]
    api_ids = [l["id"] for l in api_lists]
    assert api_ids == [list_b.id, list_a.id, list_c.id]
