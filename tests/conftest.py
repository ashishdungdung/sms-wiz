from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from sms_wiz.db import connect
from sms_wiz.merchants import MerchantDictionary
from sms_wiz.pipeline import Ingestor

IST = timezone(timedelta(hours=5, minutes=30))

#: A fixed instant so date resolution and month buckets are deterministic.
NOW = datetime(2025, 3, 1, 10, 0, tzinfo=IST)


@pytest.fixture(autouse=True)
def no_llm_env(monkeypatch):
    """Tests must never reach the network by accident."""
    for key in list(os.environ):
        if key.startswith("SMSWIZ_LLM"):
            monkeypatch.delenv(key, raising=False)


@pytest.fixture
def db_path(tmp_path) -> str:
    return str(tmp_path / "sms_wiz_test.db")


@pytest.fixture
def merchants(db_path) -> MerchantDictionary:
    return MerchantDictionary(db_path)


@pytest.fixture
def ingestor(db_path) -> Ingestor:
    inst = Ingestor(db_path)
    assert not inst.enricher.enabled
    return inst


@pytest.fixture
def conn(db_path):
    connection = connect(db_path)
    yield connection
    connection.close()
