"""The contract in ``contracts/`` must never drift from the code.

These tests are the reason the harness is IDE-agnostic: whatever editor a
contributor uses, Python is the referee for the schema and the Swift raw values.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import export_contracts  # noqa: E402
import verify_contracts  # noqa: E402

from sms_wiz.models import SmsEvent  # noqa: E402


def test_generated_contracts_are_current():
    stale = []
    for path, expected in export_contracts.build().items():
        if not path.exists() or path.read_text(encoding="utf-8") != expected:
            stale.append(path.name)
    assert not stale, f"stale contract files {stale}; run `python scripts/export_contracts.py`"


def test_cross_language_verification_passes():
    assert verify_contracts.verify() == []


def test_schema_covers_every_model_field():
    schema = json.loads((ROOT / "contracts" / "sms_event.schema.json").read_text(encoding="utf-8"))
    assert set(schema["properties"]) == set(SmsEvent.model_fields)
    assert "sender" in schema["required"]
    # ``body`` has a default so it is not required, but it must stay in the schema.
    assert "body" in schema["properties"]


def test_enum_lists_have_no_duplicates():
    enums = json.loads((ROOT / "contracts" / "enums.json").read_text(encoding="utf-8"))
    for key, values in enums.items():
        if key.startswith("_"):
            continue
        assert len(values) == len(set(values)), f"{key} has duplicate values"
        assert values, f"{key} is empty"


@pytest.mark.parametrize("key", list(export_contracts.ENUM_FILES))
def test_python_enum_matches_swift_text(key):
    """Fail early with a readable message if only Swift was edited."""
    swift = (ROOT / "ios/Packages/SMSCore/Sources/SMSCore/Contracts.swift").read_text(encoding="utf-8")
    values = verify_contracts._swift_enum_values(swift, verify_contracts.SWIFT_ENUMS[key])
    assert values == set(export_contracts.ENUM_FILES[key])


def test_fixtures_are_well_formed():
    fixtures = sorted((ROOT / "contracts" / "fixtures").glob("*.json"))
    assert fixtures
    seen = set()
    for path in fixtures:
        payload = json.loads(path.read_text(encoding="utf-8"))
        assert payload["id"] not in seen, f"duplicate fixture id {payload['id']}"
        seen.add(payload["id"])
        assert payload["input"]["body"].strip()
        assert payload["input"]["sender"].strip()
        assert payload["expect"]["intent"]
