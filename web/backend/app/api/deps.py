import uuid
from collections.abc import Generator
from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jwt.exceptions import InvalidTokenError
from pydantic import ValidationError
from sqlmodel import Session

from app.core import security
from app.core.config import settings
from app.core.db import engine
from app.models import TokenPayload, User, UserRole

reusable_oauth2 = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_STR}/login/access-token"
)


def get_db() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


SessionDep = Annotated[Session, Depends(get_db)]
TokenDep = Annotated[str, Depends(reusable_oauth2)]


def get_current_user(session: SessionDep, token: TokenDep) -> User:
    try:
        payload = jwt.decode(
            token,
            settings.SECRET_KEY.get_secret_value(),
            algorithms=[security.ALGORITHM],
        )
        token_data = TokenPayload(**payload)
        if token_data.sub is None:
            raise ValueError("Missing token subject")
        user_id = uuid.UUID(token_data.sub)
    except (InvalidTokenError, ValidationError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Could not validate credentials",
        )
    user = session.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if not user.is_active:
        raise HTTPException(status_code=400, detail="Inactive user")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def get_current_active_superuser(current_user: CurrentUser) -> User:
    if not current_user.is_superuser and current_user.role != UserRole.admin:
        raise HTTPException(
            status_code=403, detail="The user doesn't have enough privileges"
        )
    return current_user


def get_current_app_user(current_user: CurrentUser) -> User:
    if current_user.role not in {UserRole.user, UserRole.data_admin, UserRole.admin}:
        raise HTTPException(
            status_code=403, detail="The user doesn't have enough privileges"
        )
    return current_user


def get_current_data_user(current_user: CurrentUser) -> User:
    if not current_user.is_superuser and current_user.role not in {
        UserRole.data_admin,
        UserRole.admin,
    }:
        raise HTTPException(
            status_code=403, detail="The user doesn't have data privileges"
        )
    return current_user
