"""Test isolation: the extraction backend comes from each test, never from the developer's shell or .env.

Without this, RECEIPTS_EXTRACT_BACKEND=modal in a developer's .env (private mode, docs/open-weights.md) would switch
every extraction test to the Modal backend.
"""
import os

import pytest

from receipts import extract


@pytest.fixture(autouse=True)
def default_extraction_backend(monkeypatch, tmp_path_factory):
    for name in [k for k in os.environ if k.startswith(("RECEIPTS_EXTRACT_", "RECEIPTS_MODAL_"))]:
        monkeypatch.delenv(name)
    monkeypatch.setattr(extract, "DOTENV_PATH", tmp_path_factory.getbasetemp() / "no-such.env")
