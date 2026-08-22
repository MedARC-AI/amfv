from fastapi import APIRouter

from app.api.routes import (
    admin,
    create,
    home,
    invites,
    login,
    nice,
    private,
    review,
    users,
    utils,
)
from app.core.config import settings

api_router = APIRouter()
api_router.include_router(login.router)
api_router.include_router(invites.router)
api_router.include_router(users.router)
api_router.include_router(utils.router)
api_router.include_router(home.router)
api_router.include_router(review.router)
api_router.include_router(create.router)
api_router.include_router(admin.router)
api_router.include_router(nice.router)


if settings.ENVIRONMENT == "local":
    api_router.include_router(private.router)
