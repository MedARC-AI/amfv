from amfv_datasets.nice_ingest import build_record, normalize_text


def test_normalize_text_compacts_whitespace() -> None:
    text = "Line one.   Line two.\n\n\n\nLine three."
    assert normalize_text(text) == "Line one. Line two.\n\nLine three."


def test_build_record_ignores_non_nice_source() -> None:
    row = {
        "id": "123",
        "source": "who",
        "title": "Example",
        "clean_text": "Some text",
    }

    assert build_record(row) is None


def test_build_record_uses_clean_text() -> None:
    row = {
        "id": "abc",
        "source": "nice",
        "title": "NICE example",
        "url": "https://example.com",
        "overview": "Overview",
        "clean_text": "This is a NICE guideline.\n\nIt has recommendations.",
        "raw_text": "Raw text should not be used when clean_text exists.",
    }

    record = build_record(row)

    assert record is not None
    assert record["doc_id"] == "abc"
    assert record["source"] == "nice"
    assert record["title"] == "NICE example"
    assert record["text"] == "This is a NICE guideline.\n\nIt has recommendations."
    assert record["text_char_count"] == len(record["text"])