"""The shipped examples run against a local server, so they cannot rot."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from fake_server import PAGES_TEXT, RESULT_PDF, FakeApi

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def api():
    with FakeApi(polls_before_done=0) as server:
        yield server


def run_example(api, name, *args):
    env = {**os.environ, "FASTOCR_API_KEY": "fok_test", "FASTOCR_BASE_URL": api.base_url}
    return subprocess.run(
        [sys.executable, str(EXAMPLES / name), *map(str, args)],
        env=env, capture_output=True, text=True, timeout=60,
    )


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "scan.pdf"
    path.write_bytes(b"%PDF-1.4 example")
    return path


def test_extract_text(api, pdf):
    result = run_example(api, "extract_text.py", pdf)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == PAGES_TEXT


def test_searchable_pdf(api, pdf, tmp_path):
    out = tmp_path / "searchable.pdf"

    result = run_example(api, "searchable_pdf.py", pdf, out)

    assert result.returncode == 0, result.stderr
    assert out.read_bytes() == RESULT_PDF


def test_handle_errors_prints_the_text_on_success(api, pdf):
    result = run_example(api, "handle_errors.py", pdf)

    assert result.returncode == 0, result.stderr
    assert PAGES_TEXT in result.stdout


def test_handle_errors_reports_a_failed_document(pdf):
    with FakeApi(polls_before_done=0, final_status="failed") as api:
        result = run_example(api, "handle_errors.py", pdf)

    assert result.returncode == 1
    assert "processing_failed" in result.stderr


def test_async_batch_writes_a_text_file_per_pdf(api, tmp_path):
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (tmp_path / name).write_bytes(b"%PDF-1.4 " + name.encode())

    result = run_example(api, "async_batch.py", tmp_path)

    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in tmp_path.glob("*.txt")) == ["a.txt", "b.txt", "c.txt"]
    assert (tmp_path / "a.txt").read_text(encoding="utf-8") == PAGES_TEXT
