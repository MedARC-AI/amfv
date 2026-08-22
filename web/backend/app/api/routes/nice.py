from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlmodel import select

from app.api.deps import SessionDep, get_current_app_user
from app.models import Dataset, EvalType, NiceDownload, User
from app.services.nice import (
    NiceFetchError,
    get_or_create_nice_dataset,
    guidance_ref_from_url,
    materialize_nice_document,
)

router = APIRouter(prefix="/nice", tags=["nice"])

AppUser = Annotated[User, Depends(get_current_app_user)]


class NiceRecommendationRequest(BaseModel):
    # Omit to land the document in the shared "NICE Webscrape" dataset.
    dataset_id: int | None = None


class NiceUrlRecommendationRequest(NiceRecommendationRequest):
    url: str


class NiceDocumentResponse(BaseModel):
    document_id: int
    dataset_id: int
    reference: str
    title: str
    section_count: int
    char_count: int
    page_url: str


class NiceDownloadSummary(BaseModel):
    id: int
    reference: str
    title: str
    page_url: str
    page_count: int
    char_count: int


@router.post("/recommendation-url", response_model=NiceDocumentResponse)
def fetch_nice_recommendation_by_url(
    body: NiceUrlRecommendationRequest,
    session: SessionDep,
    current_user: AppUser,
) -> Any:
    """Materialize a locally imported NICE recommendation URL."""
    dataset = _retrieval_dataset(session, body.dataset_id)

    try:
        ref = guidance_ref_from_url(body.url)
    except NiceFetchError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    _ = current_user
    download = session.exec(
        select(NiceDownload).where(NiceDownload.reference == ref.ref)
    ).first()
    if download is None:
        raise HTTPException(
            status_code=404,
            detail="This NICE recommendation has not been imported yet",
        )

    document = materialize_nice_document(
        session, dataset_id=dataset.id, download=download
    )
    session.commit()
    session.refresh(document)

    return NiceDocumentResponse(
        document_id=document.id or 0,
        dataset_id=dataset.id or 0,
        reference=download.reference,
        title=download.title,
        section_count=download.page_count,
        char_count=download.char_count,
        page_url=download.page_url,
    )


@router.get("/downloads", response_model=list[NiceDownloadSummary])
def list_nice_downloads(session: SessionDep, current_user: AppUser) -> Any:
    """List previously downloaded NICE recommendations (metadata only)."""
    _ = current_user
    downloads = session.exec(
        select(NiceDownload).order_by(NiceDownload.created_at.desc())  # type: ignore[attr-defined]
    ).all()
    return [
        NiceDownloadSummary(
            id=d.id or 0,
            reference=d.reference,
            title=d.title,
            page_url=d.page_url,
            page_count=d.page_count,
            char_count=d.char_count,
        )
        for d in downloads
    ]


def _retrieval_dataset(session: SessionDep, dataset_id: int | None) -> Dataset:
    if dataset_id is None:
        return get_or_create_nice_dataset(session)
    dataset = session.get(Dataset, dataset_id)
    if dataset is None or not dataset.is_active or dataset.eval_type != EvalType.RETRIEVAL:
        raise HTTPException(status_code=404, detail="Retrieval dataset not found")
    return dataset
