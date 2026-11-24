"""CLI surface: the commands a person actually types."""

from __future__ import annotations

import pytest

from sms_wiz import cli


def run(capsys, *argv: str) -> tuple[int, str]:
    code = cli.main(list(argv))
    return code, capsys.readouterr().out


class TestDemo:
    def test_demo_runs_the_whole_corpus(self, capsys, tmp_path):
        code, out = run(capsys, "demo", "--db", str(tmp_path / "demo.db"))
        assert code == 0
        assert f"{len(cli.SAMPLE_MESSAGES)} messages processed" in out
        assert "ledger for" in out
        assert "subscriptions:" in out
        assert "credit cards" in out
        assert "digest:" in out


class TestIngest:
    def test_ingest_prints_the_decision(self, capsys, tmp_path):
        code, out = run(
            capsys,
            "ingest",
            "-s",
            "AD-HDFCBK-T",
            "-b",
            "Rs.1,450.00 debited from A/c XX4092 to VPA KIRANAKART@axisbank. Avl Bal: Rs.5,000.00.",
            "--db",
            str(tmp_path / "one.db"),
        )
        assert code == 0
        assert "expense" in out
        assert "counts_toward_spend=True" in out

    def test_json_mode_is_machine_readable(self, capsys, tmp_path):
        import json

        code, out = run(
            capsys,
            "ingest",
            "-s",
            "AD-HDFCBK-T",
            "-b",
            "Rs.500.00 debited from A/c XX4092 to VPA SWIGGY@icici",
            "--json",
            "--db",
            str(tmp_path / "one.db"),
        )
        assert code == 0
        assert json.loads(out)["event"]["intent"] == "expense"


class TestRulesCheck:
    def test_shipped_rules_pass(self, capsys):
        code, out = run(capsys, "rules-check")
        assert code == 0
        assert "rules OK" in out

    def test_broken_rules_fail_loudly(self, capsys, tmp_path):
        bad = tmp_path / "rules.json"
        bad.write_text('{"syntax": "sms-wiz.filter/v1", "rules": [{"id": "x", "when": {"any": ["a"]}, "then": {"action": "delete"}}]}')
        code, out = run(capsys, "rules-check", "--path", str(bad))
        assert code == 1
        assert "rejected" in out


class TestDerivedViews:
    @pytest.fixture
    def seeded_db(self, tmp_path, monkeypatch):
        db_path = str(tmp_path / "cli.db")
        cli.Ingestor(db_path).ingest(
            cli.IngestRequest(
                sender="AD-HDFCBK-T",
                body="Rs.1,450.00 debited from A/c XX4092 on 25-02-25 to VPA KIRANAKART@axisbank. Avl Bal: Rs.5,000.00.",
            )
        )
        cli.Ingestor(db_path).ingest(
            cli.IngestRequest(
                sender="SBICRD-T",
                body="Total due Rs 12,480.00 on your SBI Card XX4433. Minimum due Rs 620.00. Due date 05-03-25.",
            )
        )
        monkeypatch.setenv("SMSWIZ_DB", db_path)
        return db_path

    def test_analytics_prints_the_month(self, capsys, seeded_db):
        code, out = run(capsys, "analytics", "--month", "2025-02")
        assert code == 0
        assert "spend ₹1,450.00" in out
        assert "essential share" in out

    def test_cards_lists_the_statement(self, capsys, seeded_db):
        code, out = run(capsys, "cards")
        assert code == 0
        assert "SBI Card XX4433" in out
        assert "12,480.00" in out

    def test_subscriptions_survives_an_empty_calendar(self, capsys, seeded_db):
        code, out = run(capsys, "subscriptions")
        assert code == 0
        assert "recurring" in out

    def test_merchants_search_and_stats(self, capsys, tmp_path):
        code, out = run(capsys, "merchants", "--q", "swiggy")
        assert code == 0 and "SWIGGY" in out
        code, out = run(capsys, "merchants")
        assert code == 0 and "keys" in out

    def test_digest_needs_no_data_to_run(self, capsys, tmp_path, monkeypatch):
        monkeypatch.setenv("SMSWIZ_DB", str(tmp_path / "empty.db"))
        code, out = run(capsys, "digest")
        assert code == 0
        assert "nothing needs you" in out
