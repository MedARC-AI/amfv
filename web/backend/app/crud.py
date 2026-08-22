import uuid
from datetime import datetime, timezone
from hashlib import sha256
from secrets import token_urlsafe
from typing import Any

from sqlmodel import Session, col, select

from app.core.security import get_password_hash, verify_password
from app.models import (
    SignupInvite,
    SignupInviteCreate,
    User,
    UserCreate,
    UserRegister,
    UserUpdate,
    UserUpdateMe,
)


def _build_user(*, user_create: UserCreate) -> User:
    return User.model_validate(
        user_create, update={"hashed_password": get_password_hash(user_create.password)}
    )


def create_user(*, session: Session, user_create: UserCreate) -> User:
    db_obj = _build_user(user_create=user_create)
    session.add(db_obj)
    session.commit()
    session.refresh(db_obj)
    return db_obj


def hash_invite_token(token: str) -> str:
    return sha256(token.encode("utf-8")).hexdigest()


def create_signup_invite(
    *,
    session: Session,
    invite_create: SignupInviteCreate,
    created_by_user_id: uuid.UUID | None,
) -> tuple[SignupInvite, str]:
    raw_token = token_urlsafe(32)
    db_invite = SignupInvite.model_validate(
        invite_create,
        update={
            "token_hash": hash_invite_token(raw_token),
            "created_by_user_id": created_by_user_id,
        },
    )
    session.add(db_invite)
    session.commit()
    session.refresh(db_invite)
    return db_invite, raw_token


def get_signup_invite_by_token(
    *, session: Session, token: str
) -> SignupInvite | None:
    statement = select(SignupInvite).where(
        SignupInvite.token_hash == hash_invite_token(token)
    )
    return session.exec(statement).first()


def list_signup_invites(*, session: Session) -> list[SignupInvite]:
    statement = select(SignupInvite).order_by(col(SignupInvite.created_at).desc())
    return list(session.exec(statement).all())


def is_signup_invite_redeemable(invite: SignupInvite) -> bool:
    if invite.disabled_at is not None:
        return False
    if invite.expires_at is not None:
        expires_at = invite.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= datetime.now(timezone.utc):
            return False
    return invite.redeemed_count < invite.max_redemptions


def redeem_signup_invite(
    *, session: Session, invite: SignupInvite, user_register: UserRegister
) -> User:
    user_create = UserCreate(
        email=user_register.email,
        password=user_register.password,
        full_name=user_register.full_name,
        discord_handle=user_register.discord_handle,
        medical_profession=user_register.medical_profession,
        role=invite.role,
    )
    user = _build_user(user_create=user_create)
    invite.redeemed_count += 1
    session.add(user)
    session.add(invite)
    session.commit()
    session.refresh(user)
    return user


def disable_signup_invite(*, session: Session, invite: SignupInvite) -> SignupInvite:
    invite.disabled_at = datetime.now(timezone.utc)
    session.add(invite)
    session.commit()
    session.refresh(invite)
    return invite


def update_user(
    *, session: Session, db_user: User, user_in: UserUpdate | UserUpdateMe
) -> Any:
    user_data = user_in.model_dump(exclude_unset=True)
    extra_data = {}
    if "password" in user_data:
        password = user_data["password"]
        hashed_password = get_password_hash(password)
        extra_data["hashed_password"] = hashed_password
    db_user.sqlmodel_update(user_data, update=extra_data)
    session.add(db_user)
    session.commit()
    session.refresh(db_user)
    return db_user


def get_user_by_email(*, session: Session, email: str) -> User | None:
    statement = select(User).where(User.email == email)
    session_user = session.exec(statement).first()
    return session_user


# Dummy hash to use for timing attack prevention when user is not found
# This is an Argon2 hash of a random password, used to ensure constant-time comparison
DUMMY_HASH = "$argon2id$v=19$m=65536,t=3,p=4$MjQyZWE1MzBjYjJlZTI0Yw$YTU4NGM5ZTZmYjE2NzZlZjY0ZWY3ZGRkY2U2OWFjNjk"


def authenticate(*, session: Session, email: str, password: str) -> User | None:
    db_user = get_user_by_email(session=session, email=email)
    if not db_user:
        # Prevent timing attacks by running password verification even when user doesn't exist
        # This ensures the response time is similar whether or not the email exists
        verify_password(password, DUMMY_HASH)
        return None
    verified, updated_password_hash = verify_password(password, db_user.hashed_password)
    if not verified:
        return None
    if updated_password_hash:
        db_user.hashed_password = updated_password_hash
        session.add(db_user)
        session.commit()
        session.refresh(db_user)
    return db_user
