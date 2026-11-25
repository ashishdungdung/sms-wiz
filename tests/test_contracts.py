"""The contract in ``contracts/`` must never drift from the code.

These tests are the reason the harness is IDE-agnostic: whatever editor a
contributor uses, Python is the referee for the schema and the Swift raw values.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

CONTRACTS = ROOT / "contracts"

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


def test_sender_kinds_contract_matches_the_registry():
    """The extension resolves a header to a sender kind from this file alone.

    If the file drifts — or if an alias that is not header-shaped leaks into it —
    the extension and the server disagree about who sent a message, and every
    rule keyed on `sender_kinds` fires differently on the two sides.
    """
    from sms_wiz import registry
    from sms_wiz.extract import parse_sender

    payload = json.loads((CONTRACTS / "sender_kinds.json").read_text(encoding="utf-8"))
    assert payload["syntax"] == "sms-wiz.sender_kinds/v1"
    assert payload["default"] == "unknown"
    codes = payload["codes"]
    registered = {e.code for e in registry.UNIQUE_ENTITIES if e.code}
    assert set(codes) >= registered

    # No key may be something `parse_sender` would reject as a header.
    assert all(re.fullmatch(r"[A-Z0-9]{2,9}", key) for key in codes)

    for entity in registry.UNIQUE_ENTITIES:
        if not entity.code:
            continue
        assert codes[entity.code] == entity.kind.value, entity.code

    for header in ["AD-HDFCBK-T", "VM-HDFCBK", "HDFCBK", "AX-SBICRD", "AD-CDSL-S"]:
        code = parse_sender(header).entity_code
        assert codes[code] == parse_sender(header).entity_kind.value, header


def test_swift_import_sources_match_the_ingest_literal():
    """`APIClient.ImportSource` vs `IngestRequest.source`.

    The share extension sends one of these strings; a value the server rejects is
    a 422 in the user's face, and a value the server accepts but the client never
    sends is a lie in the docs. Keep them the same list.
    """
    from typing import get_args

    from sms_wiz.models import IngestRequest

    swift = (ROOT / "ios/Packages/SMSCore/Sources/SMSCore/APIClient.swift").read_text(encoding="utf-8")
    block = re.search(r"enum ImportSource: String[^{]*\{(.*?)\n    \}", swift, re.DOTALL)
    assert block, "ImportSource enum not found in APIClient.swift"
    cases = re.findall(r'case\s+(\w+)(?:\s*=\s*"([^"]+)")?', block.group(1))
    swift_values = {raw or name for name, raw in cases}

    literal = IngestRequest.model_fields["source"].annotation
    server_values = set(get_args(literal))
    assert swift_values == server_values
