import os
from sqlalchemy.orm import Session
from .models import User
from .security import hash_password

def ensure_admin_user(db: Session) -> None:
    """Bootstrap an admin user.

    Defaults:
      ADMIN_EMAIL=admin@local
      ADMIN_PASSWORD=admin
      ADMIN_NAME=Admin

    If ADMIN_UPDATE=1, we will update an existing bootstrap admin user to match env vars.
    """
    desired_email = (os.getenv("ADMIN_EMAIL") or "admin@local").strip().lower()
    desired_password = os.getenv("ADMIN_PASSWORD") or "admin"
    desired_name = os.getenv("ADMIN_NAME") or "Admin"
    do_update = (os.getenv("ADMIN_UPDATE") or "0").strip() == "1"

    # Prefer finding the desired email first
    user = db.query(User).filter(User.email == desired_email).first()

    # Fallback: find the default bootstrap user if they haven't changed email yet
    default_user = db.query(User).filter(User.email == "admin@local").first()

    if user is None and default_user is None:
        # Fresh install
        user = User(
            email=desired_email,
            password_hash=hash_password(desired_password),
            display_name=desired_name,
        )
        db.add(user)
        db.commit()
        return

    # If desired email doesn't exist but default exists, allow a one-time migration
    if user is None and default_user is not None and do_update:
        default_user.email = desired_email
        default_user.display_name = desired_name
        default_user.password_hash = hash_password(desired_password)
        db.commit()
        return

    # If user exists and update requested, update name/password
    if user is not None and do_update:
        user.display_name = desired_name
        user.password_hash = hash_password(desired_password)
        db.commit()
        return

    # Otherwise: leave as-is
    return
