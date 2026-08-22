"""Persistence operations for controlled signup invitations."""

import uuid
from datetime import datetime, timezone
from hashlib import sha256
from secrets import token_urlsafe

from sqlalchemy import func, or_, update
from sqlmodel import Session, col, select

from app.core.security import get_password_hash
from app.models import SignupInvite, SignupInviteCreate, User, UserRegister

__all__ = [
    "create_signup_invite",
    "disable_signup_invite",
    "get_redeemable_signup_invite",
    "hash_invite_token",
    "list_signup_invites",
    "redeem_signup_invite",
]


def hash_invite_token(token: str) -> str:
    """Return the non-reversible digest stored for an invitation token."""
    return sha256(token.encode("utf-8")).hexdigest()


def create_signup_invite(
    *,
    session: Session,
    invite_create: SignupInviteCreate,
    created_by_user_id: uuid.UUID | None,
) -> tuple[SignupInvite, str]:
    """Create an invitation and return its one-time display token."""
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


def get_redeemable_signup_invite(
    *, session: Session, token: str
) -> SignupInvite | None:
    """Return a redeemable invitation without exposing why others are unavailable."""
    statement = select(SignupInvite).where(
        SignupInvite.token_hash == hash_invite_token(token),
        col(SignupInvite.disabled_at).is_(None),
        or_(
            col(SignupInvite.expires_at).is_(None),
            col(SignupInvite.expires_at) > func.now(),
        ),
        SignupInvite.redeemed_count < SignupInvite.max_redemptions,
    )
    return session.exec(statement).first()


def list_signup_invites(*, session: Session) -> list[SignupInvite]:
    """List invitations newest first."""
    statement = select(SignupInvite).order_by(col(SignupInvite.created_at).desc())
    return list(session.exec(statement).all())


def redeem_signup_invite(
    *, session: Session, token: str, user_register: UserRegister
) -> User | None:
    """Atomically claim invitation capacity and insert the invited user.

    The password hash is deliberately calculated before the conditional write so
    SQLite does not hold its write lock during the expensive hash operation.
    Any user insert error rolls the claim back in the same transaction.
    """
    hashed_password = get_password_hash(user_register.password)
    claim = (
        update(SignupInvite)
        .where(
            col(SignupInvite.token_hash) == hash_invite_token(token),
            col(SignupInvite.disabled_at).is_(None),
            or_(
                col(SignupInvite.expires_at).is_(None),
                col(SignupInvite.expires_at) > func.now(),
            ),
            col(SignupInvite.redeemed_count) < col(SignupInvite.max_redemptions),
        )
        .values(redeemed_count=col(SignupInvite.redeemed_count) + 1)
        .returning(col(SignupInvite.role))
    )

    try:
        claimed_role = session.exec(claim).scalars().one_or_none()
        if claimed_role is None:
            session.rollback()
            return None

        user = User(
            email=user_register.email,
            hashed_password=hashed_password,
            full_name=user_register.full_name,
            discord_handle=user_register.discord_handle,
            medical_profession=user_register.medical_profession,
            role=claimed_role,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        return user
    except Exception:
        session.rollback()
        raise


def disable_signup_invite(*, session: Session, invite: SignupInvite) -> SignupInvite:
    """Disable an invitation and persist the change."""
    invite.disabled_at = datetime.now(timezone.utc)
    session.add(invite)
    session.commit()
    session.refresh(invite)
    return invite
