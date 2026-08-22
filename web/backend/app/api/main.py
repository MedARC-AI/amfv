from fastapi import APIRouter

from app.api.routes import (
    admin,
    create,
    home,
    invites,
    login,
    nice,
    review,
    users,
    utils,
)

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
