import pytest

from app.chunking import chunk_pages, split_text
from app.pdf_loader import Page

KW = {"size": 100, "overlap": 20, "page_limit": 150}


def test_short_page_stays_one_chunk():
    chunks = chunk_pages([Page(1, "a short slide")], "d.pdf", **KW)
    assert [(c.page, c.index, c.text) for c in chunks] == [(1, 0, "a short slide")]


def test_page_at_limit_is_not_split():
    text = "x" * KW["page_limit"]
    assert len(chunk_pages([Page(1, text)], "d.pdf", **KW)) == 1


def test_long_page_is_split_and_keeps_its_page_number():
    text = " ".join(f"word{i}" for i in range(80))  # ~500 chars
    chunks = chunk_pages([Page(7, text)], "d.pdf", **KW)
    assert len(chunks) > 1
    assert {c.page for c in chunks} == {7}
    assert [c.index for c in chunks] == list(range(len(chunks)))
    assert all(len(c.text) <= KW["size"] for c in chunks)


def test_empty_pages_are_skipped():
    assert chunk_pages([Page(1, ""), Page(2, "text")], "d.pdf", **KW)[0].page == 2


def test_chunk_ids_are_unique_and_include_source():
    pages = [Page(1, "a " * 200), Page(2, "b " * 200)]
    ids = [c.id for c in chunk_pages(pages, "d.pdf", **KW)]
    assert len(ids) == len(set(ids))
    assert all(i.startswith("d.pdf:") for i in ids)


def test_split_text_loses_no_words_and_overlaps():
    words = [f"w{i}" for i in range(200)]
    pieces = split_text(" ".join(words), size=100, overlap=20)
    seen = set()
    for piece in pieces:
        seen.update(piece.split())
    assert seen == set(words)  # nothing dropped
    # neighbouring pieces share text
    assert pieces[0].split()[-1] in pieces[1].split()


def test_split_text_prefers_line_breaks():
    text = "line one is here\n" * 10
    pieces = split_text(text, size=50, overlap=5)
    assert all(not p.endswith("lin") for p in pieces)


def test_split_text_terminates_on_unbroken_text():
    assert len(split_text("x" * 1000, size=100, overlap=20)) >= 10


def test_split_text_rejects_bad_overlap():
    with pytest.raises(ValueError):
        split_text("hello", size=10, overlap=10)
