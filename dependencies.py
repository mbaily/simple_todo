from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from database import get_session_maker
from auth import get_authenticated_user
from config import BASE_DIR, DISABLE_OFFLINE_CACHE

# Initialize templates
# This is moved here to be shared across routers and main.py
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
templates.env.globals['max'] = max
templates.env.globals['min'] = min
templates.env.globals["disable_offline_cache"] = DISABLE_OFFLINE_CACHE


def get_db(request: Request):
    """Dependency for getting database session, scoped by authenticated user."""
    user = get_authenticated_user(request)
    SessionLocal = get_session_maker(user)
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
