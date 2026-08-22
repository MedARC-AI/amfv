from datetime import timedelta
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import Field
from sqlalchemy.exc import IntegrityError

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
from app.services import invites
from app.services.rate_limit import check_rate_limit

router = APIRouter(prefix="/auth", tags=["auth"])
INVITE_UNAVAILABLE_DETAIL = "Invite unavailable"


class InviteSignupRequest(UserRegister):
    """Signup payload that lets the route normalize malformed invite tokens."""

    invite_token: str = Field(max_length=255)


def _get_redeemable_invite(session: SessionDep, token: str) -> SignupInvite:
    invite = invites.get_redeemable_signup_invite(session=session, token=token)
    if invite is None:
        raise HTTPException(status_code=404, detail=INVITE_UNAVAILABLE_DETAIL)
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
    invite, token = invites.create_signup_invite(
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
    signup_invites = invites.list_signup_invites(session=session)
    data = [SignupInvitePublic.model_validate(invite) for invite in signup_invites]
    return SignupInvitesPublic(data=data, count=len(data))


@router.post("/invites/preview", response_model=SignupInvitePreview)
def preview_invite(
    *,
    request: Request,
    session: SessionDep,
    token: Annotated[str, Body(embed=True, max_length=255)],
) -> Any:
    """
    Preview an invite while keeping its secret token in the request body.
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
    return invites.disable_signup_invite(session=session, invite=invite)


@router.post("/signup", response_model=Token)
def invite_signup(
    *, request: Request, session: SessionDep, user_in: InviteSignupRequest
) -> Any:
    """
    Create an account only when a valid invite token is supplied.
    """
    check_rate_limit(
        request,
        scope="invite-signup:ip",
        limit=settings.AUTH_RATE_LIMIT_INVITE_IP_ATTEMPTS,
    )

    try:
        user = invites.redeem_signup_invite(
            session=session, token=user_in.invite_token, user_register=user_in
        )
    except IntegrityError:
        raise HTTPException(
            status_code=400,
            detail="The user with this email already exists in the system",
        )
    if user is None:
        raise HTTPException(status_code=404, detail=INVITE_UNAVAILABLE_DETAIL)

    access_token_expires = timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return Token(
        access_token=security.create_access_token(
            user.id, expires_delta=access_token_expires
        )
    )
