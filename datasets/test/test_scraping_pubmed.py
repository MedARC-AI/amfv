"""Tests for PubMed scraping helpers."""

import json

import httpx
import pytest

from amfv_datasets.scraping import base
from amfv_datasets.scraping.html import LinkMode
from amfv_datasets.scraping.pubmed import (
    LISTING_PAGE_SIZE,
    SEARCH_TERM,
    PubMedArticleRef,
    PubMedFetchError,
    build_pubmed_article_text,
    list_pubmed_guidelines,
    pubmed_ref_from_url,
    scrape_pubmed,
    scrape_pubmed_article,
)

_BODY = """
<body>
  <sec><title>Scope and purpose</title>
    <p>This guideline covers <italic>adult</italic> hypertension.</p>
    <sec><title>Guideline objectives</title><p>Reduce cardiovascular risk.</p></sec>
  </sec>
  <sec><title>Recommendations</title>
    <p>Treat to target <xref ref-type="bibr" rid="r1">[1]</xref> .</p>
    <table-wrap><label>Table 1</label>
      <caption><title>Thresholds</title></caption>
      <table>
        <thead><tr><th>Stage</th><th>Threshold</th></tr></thead>
        <tbody><tr><td>Stage 2</td><td>140/90 mmHg</td></tr></tbody>
      </table>
    </table-wrap>
    <list list-type="order"><list-item><p>Measure blood pressure.</p></list-item></list>
  </sec>
  <sec><title>Treatment algorithm</title>
    <fig id="g1-F1"><label>Figure 1</label>
      <caption><p>Stepped care algorithm for resistant hypertension.</p></caption>
      <graphic xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="g1f1.jpg"/>
    </fig>
  </sec>
  <supplementary-material content-type="local-data" id="MOESM1">
    <media xmlns:xlink="http://www.w3.org/1999/xlink" xlink:href="12345_MOESM1_ESM.docx"/>
    <caption><p>Additional file 1: Table S1. Evidence profile for each recommendation.</p></caption>
  </supplementary-material>
  <ref-list><ref id="r1"><mixed-citation>Smith J. Irrelevant citation.</mixed-citation></ref></ref-list>
</body>
"""

_LICENSE_ALI = (
    "<license><ali:license_ref>https://creativecommons.org/licenses/by/4.0/</ali:license_ref>"
    "<license-p>Open access.</license-p></license>"
)
_LICENSE_EXT_LINK = (
    "<license><license-p>Distributed under the "
    '<ext-link ext-link-type="uri" xlink:href="https://creativecommons.org/licenses/by-nc/4.0/">'
    "CC BY-NC</ext-link> license.</license-p></license>"
)
_LICENSE_TEXT_ONLY = (
    "<license><license-p>Distributed under "
    "https://creativecommons.org/licenses/by-nc-nd/4.0/ terms.</license-p></license>"
)
# 11.7% of the open-access subset carries publisher terms instead of a CC
# license, and they are not equivalent to each other.
_LICENSE_NON_CC_PERMISSIVE = (
    '<license license-type="open-access"><license-p>This article is made available via the '
    "PMC Open Access Subset for unrestricted re-use and analyses in any form or by any means "
    "with acknowledgement of the original source.</license-p></license>"
)
_LICENSE_NON_CC_RESERVED = (
    "<copyright-statement>© 2020 Elsevier Masson SAS. All rights reserved.</copyright-statement>"
    "<license><license-p>Since January 2020 Elsevier has created a COVID-19 resource centre "
    "with free information.</license-p></license>"
)


def _jats(*, body: str = _BODY, license_block: str = _LICENSE_ALI, abstract: str = "") -> str:
    """Build a minimal but structurally faithful PMC JATS response."""
    return (
        '<?xml version="1.0"?>'
        '<pmc-articleset><article article-type="review-article"'
        ' xmlns:xlink="http://www.w3.org/1999/xlink"'
        ' xmlns:ali="http://www.niso.org/schemas/ali/1.0/">'
        "<front>"
        "<journal-meta><journal-title>Journal of Guidelines</journal-title></journal-meta>"
        "<article-meta>"
        '<article-id pub-id-type="pmid">42476581</article-id>'
        '<article-id pub-id-type="pmcid">PMC13384732</article-id>'
        '<article-id pub-id-type="doi">10.1093/example.1</article-id>'
        "<title-group><article-title>Hypertension guideline</article-title></title-group>"
        f"<permissions>{license_block}</permissions>"
        f"{abstract}"
        "</article-meta></front>"
        f"{body}"
        "</article></pmc-articleset>"
    )


def _article_client(xml: str) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("efetch.fcgi")
        assert request.url.params["db"] == "pmc"
        # NCBI's usage policy asks every request to name the calling tool.
        assert request.url.params["tool"] == "amfv"
        return httpx.Response(200, content=xml.encode())

    return httpx.Client(transport=httpx.MockTransport(handler))


def _listing_handler(
    pages: dict[int, list[str]],
    *,
    total: int,
    summaries: dict[str, dict] | None = None,
    seen_offsets: list[int] | None = None,
):
    """Serve esearch/esummary/efetch for a fake corpus keyed by PMID."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        params = request.url.params
        if path.endswith("esearch.fcgi"):
            offset = int(params["retstart"])
            if seen_offsets is not None:
                seen_offsets.append(offset)
            page = offset // LISTING_PAGE_SIZE + 1
            return httpx.Response(
                200,
                text=json.dumps({"esearchresult": {"count": str(total), "idlist": pages.get(page, [])}}),
            )
        if path.endswith("esummary.fcgi"):
            uids = params["id"].split(",")
            result = {"uids": uids}
            for uid in uids:
                result[uid] = (summaries or {}).get(
                    uid,
                    {
                        "title": f"Guideline {uid}",
                        "fulljournalname": "Journal of Guidelines",
                        "pubdate": "2026 Jan 1",
                        "articleids": [
                            {"idtype": "pubmed", "value": uid},
                            {"idtype": "doi", "value": f"10.1000/{uid}"},
                            {"idtype": "pmc", "value": f"PMC{uid}"},
                        ],
                    },
                )
            return httpx.Response(200, text=json.dumps({"result": result}))
        pmcid = params["id"]
        if pmcid == "999":
            # PMC holds the record but no full text was deposited.
            return httpx.Response(200, content=_jats(body="").encode())
        return httpx.Response(200, content=_jats().encode())

    return handler


def test_list_pubmed_guidelines_resolves_pmids_to_refs_with_pmcids() -> None:
    """One esearch plus one esummary yields refs carrying full citation metadata."""
    client = httpx.Client(transport=httpx.MockTransport(_listing_handler({1: ["111", "222"]}, total=2)))

    listing = list_pubmed_guidelines(client)

    assert listing.total == 2
    assert [ref.pmid for ref in listing.refs] == ["111", "222"]
    assert listing.refs[0].pmcid == "PMC111"
    assert listing.refs[0].title == "Guideline 111"
    assert listing.refs[0].doi == "10.1000/111"
    assert listing.refs[0].page_url == "https://pubmed.ncbi.nlm.nih.gov/111/"


def test_list_pubmed_guidelines_pages_by_numeric_offset() -> None:
    """Page number maps onto `retstart`, which is what makes paging work at all."""
    seen: list[int] = []
    client = httpx.Client(transport=httpx.MockTransport(_listing_handler({3: ["333"]}, total=900, seen_offsets=seen)))

    listing = list_pubmed_guidelines(client, page=3)

    assert seen == [2 * LISTING_PAGE_SIZE]
    assert [ref.pmid for ref in listing.refs] == ["333"]


def test_list_pubmed_guidelines_excludes_retracted_publications() -> None:
    """Retracted guidelines keep the `Guideline` type, so only this clause drops them.

    PMID 37026270, a retracted 2023 rosacea practice pattern, is in the
    open-access subset today and is what this guards against re-admitting.
    """
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request.url.params["term"])
        return httpx.Response(200, text=json.dumps({"esearchresult": {"count": "0", "idlist": []}}))

    list_pubmed_guidelines(httpx.Client(transport=httpx.MockTransport(handler)))

    assert sent == [SEARCH_TERM]
    assert 'NOT "Retracted Publication"[pt]' in sent[0]


def test_list_pubmed_guidelines_returns_no_refs_past_the_end_of_the_listing() -> None:
    """An empty page terminates the scrape loop rather than raising."""
    client = httpx.Client(transport=httpx.MockTransport(_listing_handler({}, total=2)))

    listing = list_pubmed_guidelines(client, page=99)

    assert listing.refs == []
    assert listing.total == 2


def test_list_pubmed_guidelines_skips_records_with_no_pmc_copy() -> None:
    """The open-access filter should guarantee a PMC id; a record without one is dropped."""
    summaries = {
        "111": {"title": "No full text", "articleids": [{"idtype": "pubmed", "value": "111"}]},
    }
    client = httpx.Client(
        transport=httpx.MockTransport(_listing_handler({1: ["111", "222"]}, total=2, summaries=summaries))
    )

    listing = list_pubmed_guidelines(client)

    assert [ref.pmid for ref in listing.refs] == ["222"]


def test_build_pubmed_article_text_maps_jats_sections_to_nested_headings() -> None:
    """`<sec>` nesting depth becomes markdown heading depth."""
    content, section_count, title, metadata = build_pubmed_article_text(
        _article_client(_jats()),
        PubMedArticleRef(pmid="42476581", pmcid="PMC13384732"),
        link_mode=LinkMode.STRIP,
    )

    assert "## Scope and purpose" in content
    assert "### Guideline objectives" in content
    assert "## Recommendations" in content
    assert title == "Hypertension guideline"
    assert section_count == 3
    assert metadata["journal"] == "Journal of Guidelines"
    assert metadata["article_type"] == "review-article"
    assert metadata["content_length_chars"] == len(content)


def test_build_pubmed_article_text_keeps_clinical_tables_and_drops_citation_pointers() -> None:
    """Recommendation thresholds must survive; `xref` markers and reference lists must not."""
    content, _, _, _ = build_pubmed_article_text(
        _article_client(_jats()),
        PubMedArticleRef(pmid="42476581", pmcid="PMC13384732"),
    )

    assert "140/90 mmHg" in content
    assert "Measure blood pressure" in content
    # The `[1]` pointer and the reference list it points at are citation
    # apparatus, not clinical content.
    assert "[1]" not in content
    assert "Irrelevant citation" not in content
    # Unwrapping the pointer must not leave " ." behind.
    assert "Treat to target ." not in content
    assert "Treat to target." in content


def test_build_pubmed_article_text_records_figures_it_cannot_link() -> None:
    """The image URL is not derivable from the API, so keep the filename for a later pass."""
    content, _, _, metadata = build_pubmed_article_text(
        _article_client(_jats()),
        PubMedArticleRef(pmid="42476581", pmcid="PMC13384732"),
    )

    assert metadata["figures"] == [
        {
            "id": "g1-F1",
            "file": "g1f1.jpg",
            "label": "Figure 1",
            "caption": "Stepped care algorithm for resistant hypertension.",
        }
    ]
    # The caption stays readable in the text; only the unlinkable image goes.
    assert "Stepped care algorithm" in content
    assert "g1f1.jpg" not in content


def test_build_pubmed_article_text_records_supplementary_files() -> None:
    """Evidence tables live in supplementary .docx payloads the API does not return."""
    content, _, _, metadata = build_pubmed_article_text(
        _article_client(_jats()),
        PubMedArticleRef(pmid="42476581", pmcid="PMC13384732"),
    )

    assert metadata["supplements"] == [
        {
            "id": "MOESM1",
            "file": "12345_MOESM1_ESM.docx",
            "label": "",
            "caption": "Additional file 1: Table S1. Evidence profile for each recommendation.",
        }
    ]
    assert "12345_MOESM1_ESM.docx" not in content


def test_build_pubmed_article_text_does_not_repeat_the_abstract_heading() -> None:
    """JATS labels the abstract itself, which would duplicate the heading we add."""
    content, section_count, _, _ = build_pubmed_article_text(
        _article_client(_jats(abstract="<abstract><title>Abstract</title><p>Summary text.</p></abstract>")),
        PubMedArticleRef(pmid="42476581", pmcid="PMC13384732"),
    )

    assert content.startswith("## Abstract")
    assert "**Abstract**" not in content
    assert "Summary text." in content
    # Three body sections plus the abstract.
    assert section_count == 4


@pytest.mark.parametrize(
    ("license_block", "expected_name", "expected_url"),
    [
        (_LICENSE_ALI, "CC BY", "https://creativecommons.org/licenses/by/4.0/"),
        (_LICENSE_EXT_LINK, "CC BY-NC", "https://creativecommons.org/licenses/by-nc/4.0/"),
        (_LICENSE_TEXT_ONLY, "CC BY-NC-ND", "https://creativecommons.org/licenses/by-nc-nd/4.0/"),
    ],
)
def test_build_pubmed_article_text_reads_every_license_shape(
    license_block: str, expected_name: str, expected_url: str
) -> None:
    """All three shapes occur in the open-access subset, and ND changes what we may redistribute."""
    _, _, _, metadata = build_pubmed_article_text(
        _article_client(_jats(license_block=license_block)),
        PubMedArticleRef(pmid="42476581", pmcid="PMC13384732"),
    )

    assert metadata["license"] == expected_name
    assert metadata["license_url"] == expected_url


def test_build_pubmed_article_text_records_permissive_publisher_terms() -> None:
    """A publisher license is not a missing license, so the terms are kept verbatim."""
    _, _, _, metadata = build_pubmed_article_text(
        _article_client(_jats(license_block=_LICENSE_NON_CC_PERMISSIVE)),
        PubMedArticleRef(pmid="42476581", pmcid="PMC13384732"),
    )

    assert metadata["license"] == ""
    assert metadata["license_url"] == ""
    assert metadata["license_type"] == "open-access"
    assert "unrestricted re-use" in metadata["license_statement"]


def test_build_pubmed_article_text_records_a_reserved_copyright_statement() -> None:
    """COVID-era free access still reserves all rights, and that text is not inside `<license>`."""
    _, _, _, metadata = build_pubmed_article_text(
        _article_client(_jats(license_block=_LICENSE_NON_CC_RESERVED)),
        PubMedArticleRef(pmid="42476581", pmcid="PMC13384732"),
    )

    assert metadata["license"] == ""
    # Reading only `<license>` would report free access and lose the reservation.
    assert "free information" in metadata["license_statement"]
    assert "All rights reserved" in metadata["copyright_statement"]


def test_build_pubmed_article_text_rejects_a_metadata_only_record() -> None:
    """About 2% of the subset has no deposited full text."""
    with pytest.raises(PubMedFetchError):
        build_pubmed_article_text(
            _article_client(_jats(body="")),
            PubMedArticleRef(pmid="42476581", pmcid="PMC13384732"),
        )


def test_scrape_pubmed_article_normalizes_into_a_scraped_document() -> None:
    """Scraped guidelines use the shared source/external_id conventions."""
    document = scrape_pubmed_article(
        _article_client(_jats()),
        PubMedArticleRef(pmid="42476581", pmcid="PMC13384732"),
    )

    assert document.source == "pubmed"
    assert document.external_id == "pubmed-42476581"
    assert document.url == "https://pubmed.ncbi.nlm.nih.gov/42476581/"
    assert document.metadata["pmcid"] == "PMC13384732"
    assert "Scope and purpose" in document.content


def test_scrape_pubmed_pages_through_the_listing_and_terminates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Paging advances by offset, crosses a page boundary, and stops on the empty page."""
    seen: list[int] = []
    handler = _listing_handler({1: ["111"], 2: ["222"]}, total=2, seen_offsets=seen)
    monkeypatch.setattr(
        "amfv_datasets.scraping.pubmed.default_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    run = scrape_pubmed(documents=None)
    documents = list(run.documents)

    assert run.total == 2
    assert [document.external_id for document in documents] == ["pubmed-111", "pubmed-222"]
    # Page 1 is fetched once for the total and reused via `first_page_items`,
    # so the listing is not re-requested at offset 0 during the run.
    assert seen == [0, LISTING_PAGE_SIZE, 2 * LISTING_PAGE_SIZE]


def test_scrape_pubmed_skips_records_pmc_holds_without_full_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """One metadata-only record does not abort a run over the rest of the listing."""
    handler = _listing_handler({1: ["999", "222"]}, total=2)
    monkeypatch.setattr(
        "amfv_datasets.scraping.pubmed.default_client",
        lambda: httpx.Client(transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(base.time, "sleep", lambda _seconds: None)

    documents = list(scrape_pubmed(documents=None).documents)

    assert [document.external_id for document in documents] == ["pubmed-222"]


def test_eutils_retries_when_ncbi_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 429 mid-run must not kill the scrape; NCBI returns them on bursts."""
    monkeypatch.setattr("amfv_datasets.scraping.pubmed.time.sleep", lambda _seconds: None)
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        if len(attempts) < 3:
            return httpx.Response(429, text="Too Many Requests")
        return httpx.Response(200, content=_jats().encode())

    client = httpx.Client(transport=httpx.MockTransport(handler))
    document = scrape_pubmed_article(client, PubMedArticleRef(pmid="42476581", pmcid="PMC13384732"))

    assert len(attempts) == 3
    assert document.external_id == "pubmed-42476581"


def test_eutils_gives_up_after_repeated_rate_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Persistent 429s are a real stop signal, not something to retry forever."""
    monkeypatch.setattr("amfv_datasets.scraping.pubmed.time.sleep", lambda _seconds: None)
    attempts = []

    def handler(request: httpx.Request) -> httpx.Response:
        attempts.append(request.url.path)
        return httpx.Response(429, text="Too Many Requests")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.HTTPStatusError):
        scrape_pubmed_article(client, PubMedArticleRef(pmid="42476581", pmcid="PMC13384732"))

    assert len(attempts) == 4


def test_pubmed_ref_from_url_accepts_both_pubmed_and_pmc_urls() -> None:
    """Only one identifier is knowable from a URL; the other is resolved when scraping."""
    pubmed_ref = pubmed_ref_from_url("https://pubmed.ncbi.nlm.nih.gov/42476581/")
    pmc_ref = pubmed_ref_from_url("https://www.ncbi.nlm.nih.gov/pmc/articles/PMC13384732/")

    assert pubmed_ref == PubMedArticleRef(pmid="42476581")
    assert pmc_ref == PubMedArticleRef(pmcid="PMC13384732")
    assert pmc_ref.page_url == "https://www.ncbi.nlm.nih.gov/pmc/articles/PMC13384732/"


def test_scrape_pubmed_article_uses_the_same_id_from_either_entry_point() -> None:
    """A PMC URL carries no PMID, but the record does, so both routes agree."""
    from_listing = scrape_pubmed_article(
        _article_client(_jats()), PubMedArticleRef(pmid="42476581", pmcid="PMC13384732")
    )
    from_pmc_url = scrape_pubmed_article(_article_client(_jats()), PubMedArticleRef(pmcid="PMC13384732"))

    assert from_listing.external_id == from_pmc_url.external_id == "pubmed-42476581"


def test_pubmed_ref_from_url_resolves_a_pmid_to_full_text(monkeypatch: pytest.MonkeyPatch) -> None:
    """A PubMed URL carries no PMCID, so efetch needs one looked up first."""
    handler = _listing_handler({1: ["111"]}, total=1)
    client = httpx.Client(transport=httpx.MockTransport(handler))

    document = scrape_pubmed_article(client, pubmed_ref_from_url("https://pubmed.ncbi.nlm.nih.gov/111/"))

    assert document.external_id == "pubmed-111"
    assert document.metadata["pmcid"] == "PMC111"


@pytest.mark.parametrize(
    "url",
    [
        "https://example.org/42476581/",
        "https://pubmed.ncbi.nlm.nih.gov/not-a-pmid/",
        "https://www.ncbi.nlm.nih.gov/pmc/articles/13384732/",
        "ftp://pubmed.ncbi.nlm.nih.gov/42476581/",
    ],
)
def test_pubmed_ref_from_url_rejects_non_article_urls(url: str) -> None:
    """Anything that is not a PubMed or PMC article URL is an error, not a guess."""
    with pytest.raises(PubMedFetchError):
        pubmed_ref_from_url(url)
