#!/usr/bin/env python3
"""Cross-language contract guard.

Fails when any of these disagree:

    contracts/enums.json   (generated from Python)
        ==  sms_wiz/models.py enums        (live values, not a snapshot)
        ==  Swift raw values in ios/Packages/SMSCore/.../Contracts.swift
        ==  every fixture's expected values in contracts/fixtures/

Swift is checked *textually* on purpose: this runs on Linux and in CI without
Xcode or a Mac, which is exactly what an IDE-agnostic harness needs. The Swift
unit tests do the same comparison at compile time on a Mac.

Usage:
    python scripts/verify_contracts.py [--verbose]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONTRACTS = ROOT / "contracts"
SWIFT_CONTRACTS = ROOT / "ios" / "Packages" / "SMSCore" / "Sources" / "SMSCore" / "Contracts.swift"
FIXTURES = CONTRACTS / "fixtures"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import export_contracts  # noqa: E402

#: contract file key -> Swift enum name
SWIFT_ENUMS = {
    "intents": "SMSEventIntent",
    "apple_actions": "AppleAction",
    "apple_subactions": "AppleSubAction",
    "retentions": "Retention",
    "ledger_treatments": "LedgerTreatment",
    "entity_kinds": "EntityKind",
}

#: fixture field -> enum key it must belong to
FIXTURE_ENUM_FIELDS = {
    "intent": "intents",
    "apple_action": "apple_actions",
    "apple_subaction": "apple_subactions",
    "retention": "retentions",
    "ledger_treatment": "ledger_treatments",
}


def _swift_enum_values(source: str, enum_name: str) -> set[str] | None:
    match = re.search(
        rf"public enum {enum_name}\s*:\s*String[^{{]*\{{(.*?)\n\}}", source, re.DOTALL
    )
    if not match:
        return None
    body = match.group(1)
    return set(re.findall(r'case\s+\w+\s*=\s*"([^"]+)"', body))


def verify(verbose: bool = False) -> list[str]:
    errors: list[str] = []

    # 1. Committed contracts must match what the model generates today.
    for path, expected in export_contracts.build().items():
        if not path.exists():
            errors.append(f"{path.relative_to(ROOT)} missing — run scripts/export_contracts.py")
        elif path.read_text(encoding="utf-8") != expected:
            errors.append(f"{path.relative_to(ROOT)} is stale — run scripts/export_contracts.py")

    enums_path = CONTRACTS / "enums.json"
    if not enums_path.exists():
        return errors or ["contracts/enums.json missing"]
    enums = json.loads(enums_path.read_text(encoding="utf-8"))

    # 2. Snapshot == live Python enums.
    for key, current in export_contracts.ENUM_FILES.items():
        if set(enums.get(key, [])) != set(current):
            errors.append(
                f"{key}: contracts/enums.json vs Python differ by {sorted(set(enums.get(key, [])) ^ set(current))}"
            )

    # 3. Swift raw values == contract.
    if not SWIFT_CONTRACTS.exists():
        errors.append(f"{SWIFT_CONTRACTS.relative_to(ROOT)} missing")
    else:
        source = SWIFT_CONTRACTS.read_text(encoding="utf-8")
        for key, enum_name in SWIFT_ENUMS.items():
            values = _swift_enum_values(source, enum_name)
            if values is None:
                errors.append(f"Swift enum {enum_name} not found in Contracts.swift")
            elif values != set(enums.get(key, [])):
                errors.append(
                    f"Swift {enum_name} != contracts {key}, difference {sorted(values ^ set(enums.get(key, [])))}"
                )

    # 4. Fixtures use only real enum values.
    fixture_files = sorted(FIXTURES.glob("*.json"))
    if not fixture_files:
        errors.append("no fixtures found in contracts/fixtures/")
    for path in fixture_files:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"{path.name}: invalid JSON ({exc})")
            continue
        for field, key in FIXTURE_ENUM_FIELDS.items():
            expected = payload.get("expect", {}).get(field)
            if expected is None:
                continue
            if expected not in enums.get(key, []):
                errors.append(f"{path.name}: expect.{field}='{expected}' is not in {key}")
        if "input" not in payload or "body" not in payload.get("input", {}):
            errors.append(f"{path.name}: missing input.body")
        if verbose:
            print(f"  fixture {path.name}: {payload.get('expect', {}).get('intent')}")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args(argv)

    errors = verify(verbose=args.verbose)
    if errors:
        print("CONTRACT VERIFICATION FAILED")
        for error in errors:
            print(f"  - {error}")
        return 1

    count = len(list(FIXTURES.glob("*.json")))
    print(f"contracts OK — schema + Python + Swift + {count} fixtures agree")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
