from typing import Optional
from fastapi import Request
from sqlalchemy.orm import Session
from .models import User

SESSION_KEY = "user_id"

def get_current_user(request: Request, db: Session) -> Optional[User]:
    user_id = request.session.get(SESSION_KEY)
    if not user_id:
        return None
    return db.get(User, user_id)

def login_user(request: Request, user: User) -> None:
    request.session[SESSION_KEY] = user.id

def logout_user(request: Request) -> None:
    request.session.pop(SESSION_KEY, None)
