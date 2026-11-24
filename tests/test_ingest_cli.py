"""The command-line loader and the shipped sample documents."""
from pathlib import Path

import pytest

from app import ingest, store
from app.pdf_loader import load_pdf
from tests.conftest import make_pdf

SAMPLES = Path("samples")


def test_wildcards_are_expanded_because_windows_shells_do_not(tmp_path):
    for name in ("b.pdf", "a.pdf", "notes.txt"):
        (tmp_path / name).write_bytes(b"x")
    found = ingest.expand_paths([tmp_path / "*.pdf"])
    assert [p.name for p in found] == ["a.pdf", "b.pdf"]  # sorted, and only the PDFs


def test_a_pattern_matching_nothing_is_kept_so_the_error_names_it(tmp_path):
    pattern = tmp_path / "*.pdf"
    assert ingest.expand_paths([pattern]) == [pattern]


def test_plain_paths_are_left_alone(tmp_path):
    plain = tmp_path / "one.pdf"
    assert ingest.expand_paths([plain]) == [plain]


def test_the_loader_accepts_a_wildcard_end_to_end(fake_gemini, tmp_path):
    (tmp_path / "one.pdf").write_bytes(make_pdf("getopt one"))
    (tmp_path / "two.pdf").write_bytes(make_pdf("strtok two"))
    assert ingest.main([str(tmp_path / "*.pdf")]) == 0
    assert [d["source"] for d in store.list_documents()] == ["one.pdf", "two.pdf"]


def test_a_missing_file_fails_cleanly_with_exit_code_1(fake_gemini, tmp_path, capsys):
    assert ingest.main([str(tmp_path / "nope.pdf")]) == 1
    assert "FAILED" in capsys.readouterr().err


# ----- the sample documents that ship with the project -----


@pytest.mark.parametrize("pdf", sorted(SAMPLES.glob("*.pdf")), ids=lambda p: p.name)
def test_every_sample_document_is_readable_and_has_text_on_every_page(pdf):
    pages = load_pdf(pdf)
    assert len(pages) >= 4
    assert all(len(page.text) > 300 for page in pages), "a page with (almost) no text"


def test_there_are_three_samples_covering_work_and_study():
    names = {p.name for p in SAMPLES.glob("*.pdf")}
    assert names == {
        "Acme-AirLink-X200-Router-Manual.pdf",
        "Northwind-Employee-Handbook.pdf",
        "Intro-to-C-Pointers-Study-Notes.pdf",
    }


def test_the_samples_index_into_a_small_number_of_sections(fake_gemini):
    """Cheap to index on a free Gemini plan (well under one day's allowance, and about a minute)."""
    assert ingest.main([str(SAMPLES / "*.pdf")]) == 0
    assert sum(d["chunks"] for d in store.list_documents()) <= 30


def test_the_sample_generator_output_matches_the_committed_files(tmp_path, monkeypatch):
    """The PDFs in samples/ must be exactly what tools/make_sample_pdfs.py produces, so they can't drift."""
    import importlib.util

    spec = importlib.util.spec_from_file_location("make_sample_pdfs", Path("tools/make_sample_pdfs.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for filename, document in module.DOCUMENTS.items():
        expected = module.build_pdf(document["title"], document["pages"])
        assert (SAMPLES / filename).read_bytes() == expected, filename
