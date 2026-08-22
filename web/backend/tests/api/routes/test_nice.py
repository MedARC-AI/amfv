import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, delete, select

from app.core.config import settings
from app.models import Chunk, Dataset, Document, EvalType, NiceDownload
from app.services import nice as nice_service


def _listing_html(*refs: str) -> str:
    documents = [
        {
            "guidanceRef": ref,
            "title": f"{ref} title",
            "pathAndQuery": f"/guidance/{ref.lower()}",
        }
        for ref in refs
    ]
    payload = {
        "props": {
            "pageProps": {
                "results": {"resultCount": len(documents), "documents": documents}
            }
        }
    }
    return (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        f"{json.dumps(payload)}</script></body></html>"
    )


def _overview_html(slug: str) -> str:
    return (
        f"<html><head><title>{slug.upper()} title | Guidance | NICE</title></head><body>"
        "<nav class='stacked-nav'><ul class='stacked-nav__list'>"
        f"<li><a href='/guidance/{slug}/chapter/Recommendations'>Recommendations</a></li>"
        f"<li><a href='{nice_service.BASE_URL}/guidance/{slug}/chapter/Recommendations#antenatal-education'>Antenatal education</a></li>"
        f"<li><a href='/guidance/{slug}/chapter/Finding-more-information-and-committee-details'>Finding more information and committee details</a></li>"
        f"<li><a href='/guidance/{slug}/chapter/Update-information'>Update information</a></li>"
        "</ul></nav>"
        "<main>"
        f"<p><a href='{nice_service.BASE_URL}/guidance/{slug}/chapter/Recommendations-for-research'>Body link</a></p>"
        f"<a class='prev-next__link' href='/guidance/{slug}/chapter/Recommendations'>Next page Recommendations</a>"
        "</main>"
        "</body></html>"
    )


def _meditron_nav_list_html(slug: str) -> str:
    return (
        f"<html><head><title>{slug.upper()} title | Guidance | NICE</title></head><body>"
        "<ul class='nav-list'>"
        f"<li><a href='/guidance/{slug}'>Overview</a></li>"
        f"<li><a href='/guidance/{slug}/chapter/Recommendations'>Recommendations</a></li>"
        f"<li><a href='{nice_service.BASE_URL}/guidance/{slug}/chapter/Recommendations#antenatal-education'>Antenatal education</a></li>"
        f"<li><a href='/guidance/{slug}/chapter/Update-information?tab=evidence'>Update information</a></li>"
        "</ul>"
        "<main>"
        f"<p><a href='{nice_service.BASE_URL}/guidance/{slug}/chapter/Recommendations-for-research'>Body link</a></p>"
        f"<a class='prev-next__link' href='/guidance/{slug}/chapter/Recommendations'>Next page Recommendations</a>"
        "</main>"
        "</body></html>"
    )


CHAPTER_HTML = """
<html><body>
<div class="chapter">
  <h2 class="title">Recommendations</h2>
  <p>Give all women information about labour.[1]</p>
  <h3 class="title">1.1 Antenatal education</h3>
  <h4 class="recommendation__number">1.1.1</h4>
  <ul class="itemizedlist"><li>first point</li><li>second point</li></ul>
  <table>
    <thead>
      <tr><th>Fluid</th><th>Osmolarity</th><th>Sodium</th></tr>
    </thead>
    <tbody>
      <tr><td>0.9% sodium chloride</td><td>Isotonic</td><td>154</td></tr>
      <tr><td>Hartmann's solution</td><td>Isotonic</td><td>131</td></tr>
    </tbody>
  </table>
</div>
</body></html>
"""


UPDATE_HTML = """
<html><body>
<div class="chapter">
  <h2>Update information</h2>
  <p>This guideline was updated.</p>
</div>
</body></html>
"""


FINDING_MORE_HTML = """
<html><body>
<div class="chapter">
  <h2>Finding more information and committee details</h2>
  <p>Committee details.</p>
</div>
</body></html>
"""


def _mock_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/guidance/published":
            return httpx.Response(200, text=_listing_html("NG1", "CG2"))
        if path.endswith("/chapter/Update-information"):
            return httpx.Response(200, text=UPDATE_HTML)
        if path.endswith("/chapter/Finding-more-information-and-committee-details"):
            return httpx.Response(200, text=FINDING_MORE_HTML)
        if "/chapter/" in path:
            return httpx.Response(200, text=CHAPTER_HTML)
        # guidance overview page, e.g. /guidance/ng1
        slug = path.rsplit("/", 1)[-1]
        return httpx.Response(200, text=_overview_html(slug))

    return httpx.Client(
        transport=httpx.MockTransport(handler), base_url=nice_service.BASE_URL
    )


@pytest.fixture(autouse=True)
def _clean_downloads(db: Session) -> None:
    db.execute(delete(NiceDownload))
    db.commit()


def _retrieval_dataset(db: Session) -> Dataset:
    dataset = Dataset(
        name=f"nice-ds-{len(db.exec(select(Dataset)).all())}",
        display_name="NICE retrieval",
        eval_type=EvalType.RETRIEVAL,
    )
    db.add(dataset)
    db.commit()
    db.refresh(dataset)
    return dataset


def _cached_download(db: Session, reference: str = "NG1") -> NiceDownload:
    slug = reference.lower()
    download = NiceDownload(
        reference=reference,
        slug=slug,
        title=f"{reference} title",
        page_url=f"{nice_service.BASE_URL}/guidance/{slug}",
        pdf_url=f"{nice_service.BASE_URL}/guidance/{slug}",
        page_count=1,
        char_count=len(CHAPTER_HTML),
        content="# Recommendations\n\nUse local cache only.",
    )
    db.add(download)
    db.commit()
    db.refresh(download)
    return download


def test_chapter_links_use_current_stacked_nav_toc_only() -> None:
    links = nice_service._chapter_links(_overview_html("ng1"), "ng1")

    assert links == [
        f"{nice_service.BASE_URL}/guidance/ng1/chapter/Recommendations",
        f"{nice_service.BASE_URL}/guidance/ng1/chapter/Finding-more-information-and-committee-details",
        f"{nice_service.BASE_URL}/guidance/ng1/chapter/Update-information",
    ]


def test_chapter_links_fall_back_to_meditron_nav_list_toc_only() -> None:
    links = nice_service._chapter_links(_meditron_nav_list_html("ng1"), "ng1")

    assert links == [
        f"{nice_service.BASE_URL}/guidance/ng1/chapter/Recommendations",
        f"{nice_service.BASE_URL}/guidance/ng1/chapter/Update-information",
    ]


def test_build_guideline_text_scrapes_clean_markdown() -> None:
    ref = nice_service.GuidanceRef(
        ref="NG1",
        slug="ng1",
        title="NG1",
        page_url=f"{nice_service.BASE_URL}/guidance/ng1",
    )

    content, section_count, title = nice_service.build_guideline_text(
        _mock_client(), ref
    )

    assert title == "NG1 title"
    # Duplicate section anchors are deduped, the committee-details chapter is
    # skipped, and body/next-page links outside the NICE TOC are ignored.
    assert section_count == 2
    # Clean markdown structure, with the citation marker stripped.
    assert content.count("# Recommendations") == 1
    assert "# Recommendations" in content
    assert "## 1.1 Antenatal education" in content
    assert "# Update information" in content
    assert "Committee details." not in content
    assert "- first point" in content
    assert "| Fluid | Osmolarity | Sodium |" in content
    assert "| 0.9% sodium chloride | Isotonic | 154 |" in content
    assert "[1]" not in content


def test_materialize_creates_complete_document_without_chunking(db: Session) -> None:
    dataset = _retrieval_dataset(db)
    download = _cached_download(db, "NG1")

    document = nice_service.materialize_nice_document(
        db, dataset_id=dataset.id, download=download
    )
    db.commit()

    assert document.dataset_id == dataset.id
    assert document.external_id == f"nice-{download.slug}"
    chunks = db.exec(select(Chunk).where(Chunk.document_id == document.id)).all()
    assert len(chunks) == 1
    assert chunks[0].text == download.content
    assert "# Recommendations" in document.content

    # Re-materializing the same guideline reuses the existing document.
    again = nice_service.materialize_nice_document(
        db, dataset_id=dataset.id, download=download
    )
    assert again.id == document.id


def test_endpoint_requires_authentication(client: TestClient) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/nice/recommendation-url",
        json={"url": "https://www.nice.org.uk/guidance/ng1"},
    )
    assert response.status_code == 401


def test_endpoint_creates_source_document_from_url(
    client: TestClient,
    db: Session,
    normal_user_token_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dataset = _retrieval_dataset(db)
    download = _cached_download(db, "NG1")
    monkeypatch.setattr(
        nice_service,
        "build_guideline_text",
        lambda *args, **kwargs: pytest.fail("endpoint should not scrape NICE"),
    )

    response = client.post(
        f"{settings.API_V1_STR}/nice/recommendation-url",
        headers=normal_user_token_headers,
        json={
            "dataset_id": dataset.id,
            "url": "https://www.nice.org.uk/guidance/ng1",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["reference"] == download.reference
    assert data["dataset_id"] == dataset.id
    assert data["title"] == download.title

    document = db.get(Document, data["document_id"])
    assert document is not None
    assert document.external_id == "nice-ng1"
    assert document.dataset_id == dataset.id


def test_endpoint_rejects_invalid_nice_url(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/nice/recommendation-url",
        headers=normal_user_token_headers,
        json={"url": "https://example.com/not-nice"},
    )
    assert response.status_code == 409


def test_url_endpoint_rejects_non_retrieval_dataset(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/nice/recommendation-url",
        headers=normal_user_token_headers,
        json={
            "dataset_id": 999999,
            "url": "https://www.nice.org.uk/guidance/ng1",
        },
    )
    assert response.status_code == 404


def test_url_endpoint_requires_local_download(
    client: TestClient,
    normal_user_token_headers: dict[str, str],
) -> None:
    response = client.post(
        f"{settings.API_V1_STR}/nice/recommendation-url",
        headers=normal_user_token_headers,
        json={"url": "https://www.nice.org.uk/guidance/ng1"},
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "This NICE recommendation has not been imported yet"


def test_url_endpoint_defaults_to_nice_webscrape_dataset(
    client: TestClient,
    db: Session,
    normal_user_token_headers: dict[str, str],
) -> None:
    _cached_download(db, "NG1")

    response = client.post(
        f"{settings.API_V1_STR}/nice/recommendation-url",
        headers=normal_user_token_headers,
        json={"url": "https://www.nice.org.uk/guidance/ng1"},
    )
    assert response.status_code == 200
    data = response.json()

    dataset = db.get(Dataset, data["dataset_id"])
    assert dataset is not None
    assert dataset.name == nice_service.NICE_DATASET_NAME
    assert dataset.eval_type == EvalType.RETRIEVAL


def test_get_or_create_nice_dataset_is_idempotent(db: Session) -> None:
    first = nice_service.get_or_create_nice_dataset(db)
    db.commit()
    second = nice_service.get_or_create_nice_dataset(db)
    assert first.id == second.id
