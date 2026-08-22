from datetime import timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from app import crud
from app.api.deps import CurrentUser, SessionDep, get_current_active_superuser
from app.core import security
from app.core.config import settings
from app.models import (
    SignupInvite,
    SignupInviteCreate,
    SignupInviteCreated,
    SignupInvitePreview,
    SignupInvitePublic,
    SignupInvitesPublic,
    Token,
    UserRegister,
)
from app.services.rate_limit import check_rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])


def _get_redeemable_invite(session: SessionDep, token: str) -> SignupInvite:
    invite = crud.get_signup_invite_by_token(session=session, token=token)
    if invite is None or not crud.is_signup_invite_redeemable(invite):
        raise HTTPException(status_code=404, detail="Invite not found")
    return invite


@router.post(
    "/invites",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=SignupInviteCreated,
)
def create_invite(
    *,
    session: SessionDep,
    invite_in: SignupInviteCreate,
    current_user: CurrentUser,
) -> Any:
    """
    Create an invite link for controlled signup.
    """
    invite, token = crud.create_signup_invite(
        session=session,
        invite_create=invite_in,
        created_by_user_id=current_user.id,
    )
    return SignupInviteCreated.model_validate(invite, update={"token": token})


@router.get(
    "/invites",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=SignupInvitesPublic,
)
def list_invites(*, session: SessionDep) -> Any:
    """
    List all signup invites, newest first.
    """
    invites = crud.list_signup_invites(session=session)
    data = [SignupInvitePublic.model_validate(invite) for invite in invites]
    return SignupInvitesPublic(data=data, count=len(data))


@router.get("/invites/{token}", response_model=SignupInvitePreview)
def preview_invite(*, request: Request, session: SessionDep, token: str) -> Any:
    """
    Preview an invite without exposing the stored token hash.
    """
    check_rate_limit(
        request,
        scope="invite-preview:ip",
        limit=settings.AUTH_RATE_LIMIT_INVITE_IP_ATTEMPTS,
    )

    invite = _get_redeemable_invite(session, token)
    return SignupInvitePreview(
        role=invite.role,
        expires_at=invite.expires_at,
        remaining_redemptions=invite.max_redemptions - invite.redeemed_count,
    )


@router.post(
    "/invites/{invite_id}/disable",
    dependencies=[Depends(get_current_active_superuser)],
    response_model=SignupInvitePublic,
)
def disable_invite(*, session: SessionDep, invite_id: UUID) -> Any:
    """
    Disable an invite before its normal expiry or redemption limit.
    """
    invite = session.get(SignupInvite, invite_id)
    if invite is None:
        raise HTTPException(status_code=404, detail="Invite not found")
    return crud.disable_signup_invite(session=session, invite=invite)


@router.post("/signup", response_model=Token)
def invite_signup(
    *, request: Request, session: SessionDep, user_in: UserRegister
) -> Any:
    """
    Create an account only when a valid invite token is supplied.
    """
    check_rate_limit(
        request,
        scope="invite-signup:ip",
        limit=settings.AUTH_RATE_LIMIT_INVITE_IP_ATTEMPTS,
    )

    invite = _get_redeemable_invite(session, user_in.invite_token)
    user = crud.get_user_by_email(session=session, email=user_in.email)
    if user:
        raise HTTPException(
            status_code=400,
            detail="The user with this email already exists in the system",
        )
    user = crud.redeem_signup_invite(
        session=session, invite=invite, user_register=user_in
    )
    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return Token(
        access_token=security.create_access_token(
            user.id, expires_delta=access_token_expires
        )
    )
