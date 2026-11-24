"""Export: the exit door has to be plain, complete and safe to open.

Two promises are tested here. First, that a user can leave with their data in a
format that existing tools read without this project installed. Second, that
opening the export cannot execute anything — a merchant name is user-generated
text that ends up in a spreadsheet cell.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from sms_wiz import cli, export
from sms_wiz.api import create_app
from sms_wiz.db import connect

from .conftest import NOW
from .test_pipeline import ingest


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path))


@pytest.fixture
def populated(ingestor, db_path):
    ingest(
        ingestor,
        "AD-HDFCBK-T",
        "Rs.450.00 debited from A/c XX1234 on 25-02-25 to VPA swiggy@icici. "
        "UPI Ref 426512345678. Avl Bal Rs.12,340.50",
    )
    ingest(
        ingestor,
        "AD-HDFCBK-T",
        "Rs.95,000.00 credited to your A/c XX1234 towards salary. Avl Bal Rs.1,07,340.50",
        at=NOW,
    )
    return db_path


class TestCsv:
    def test_header_is_the_documented_column_order(self):
        text = export.to_csv([])
        assert text.splitlines() == [",".join(export.CSV_COLUMNS)]
        assert text.endswith("\n")

    def test_one_row_per_event_in_date_order(self, populated):
        conn = connect(populated)
        try:
            text = export.to_csv(export.event_rows(conn))
        finally:
            conn.close()
        lines = [line for line in text.splitlines() if line]
        assert len(lines) == 3  # header + two events
        assert lines[0].startswith("event_id,received_at,intent")

    def test_a_formula_in_a_merchant_name_cannot_execute(self):
        text = export.to_csv(
            [
                {
                    "event_id": "e1",
                    "received_at": "2026-03-01T10:00:00+05:30",
                    "merchant_clean": '=HYPERLINK("http://evil.example","click")',
                    "merchant_raw": "@SUM(1+1)",
                    "category": "-2+3",
                }
            ]
        )
        row = text.splitlines()[1]
        assert "'=HYPERLINK" in row
        assert "'@SUM" in row
        assert "'-2+3" in row
        # The rest of the ledger is untouched.
        assert "e1" in row and "2026-03-01" in row

    def test_quoting_survives_commas_and_quotes(self):
        text = export.to_csv([{"event_id": "e1", "merchant_clean": 'Shop, "The Best"'}])
        row = text.splitlines()[1]
        assert '"Shop, ""The Best"""' in row

    def test_message_bodies_are_not_in_the_export(self, populated):
        conn = connect(populated)
        try:
            text = export.to_csv(export.event_rows(conn))
        finally:
            conn.close()
        assert "Avl Bal" not in text
        assert "password" not in text.lower()

    def test_parse_round_trip(self, populated):
        import csv as csv_module
        import io

        conn = connect(populated)
        try:
            text = export.to_csv(export.event_rows(conn))
        finally:
            conn.close()
        parsed = list(csv_module.DictReader(io.StringIO(text)))
        assert len(parsed) == 2
        assert {row["intent"] for row in parsed} == {"expense", "income"}
        assert float(parsed[0]["amount_value"]) == pytest.approx(450.0)


class TestJsonSnapshot:
    def test_carries_a_format_tag_and_every_table(self, populated):
        conn = connect(populated)
        try:
            data = export.snapshot(conn)
        finally:
            conn.close()
        assert data["format"] == "sms-wiz.export/v1"
        for key in ("events", "refunds", "vouchers", "balances", "alerts", "merchant_dictionary"):
            assert key in data
        assert data["counts"]["events"] == 2
        assert data["counts"]["balances"] >= 1

    def test_is_stable_json(self, populated):
        conn = connect(populated)
        try:
            first = export.to_json(conn)
        finally:
            conn.close()
        data = json.loads(first)
        # Sorted keys and the same numbers a second time (ignore the timestamp).
        data.pop("exported_at")
        assert first.index('"app"') < first.index('"events"')
        assert data["counts"]["events"] == 2

    def test_month_filter_narrows_events_only(self, populated):
        conn = connect(populated)
        try:
            february = export.snapshot(conn, month="2025-02")
            march = export.snapshot(conn, month="2025-03")
        finally:
            conn.close()
        assert february["counts"]["events"] == 1
        assert march["counts"]["events"] == 1
        assert february["events"][0]["intent"] == "expense"


class TestApiExport:
    def test_csv_download_is_offered_as_a_file(self, client):
        response = client.get("/api/v1/export.csv")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")
        assert "attachment; filename=" in response.headers["content-disposition"]
        assert response.text.startswith("event_id,")

    def test_json_download_is_valid_json(self, client):
        response = client.get("/api/v1/export.json")
        assert response.status_code == 200
        assert response.json()["format"] == "sms-wiz.export/v1"

    def test_bad_month_is_rejected_not_emptied(self, client):
        assert client.get("/api/v1/export.csv?month=2026-13").status_code == 422

    def test_exports_are_behind_the_token(self, db_path, monkeypatch):
        monkeypatch.setenv("SMSWIZ_TOKEN", "s3cret")
        client = TestClient(create_app(db_path))
        assert client.get("/api/v1/export.csv").status_code == 401
        assert client.get("/api/v1/export.json").status_code == 401


class TestCliExport:
    def test_writes_a_file(self, capsys, tmp_path, populated):
        target = tmp_path / "ledger.csv"
        code = cli.main(["export", "--format", "csv", "--out", str(target), "--db", populated])
        assert code == 0
        assert "wrote" in capsys.readouterr().out
        assert target.read_text(encoding="utf-8").startswith("event_id,")

    def test_stdout_is_pipeable(self, capsys, populated):
        code = cli.main(["export", "--format", "json", "--db", populated])
        assert code == 0
        assert json.loads(capsys.readouterr().out)["format"] == "sms-wiz.export/v1"

    def test_a_bad_month_exits_nonzero(self, capsys, tmp_path):
        code = cli.main(["export", "--month", "2025-13", "--db", str(tmp_path / "x.db")])
        assert code == 2
        assert "YYYY-MM" in capsys.readouterr().err
