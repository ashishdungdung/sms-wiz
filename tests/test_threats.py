"""Tests for the spam & threat indicator database."""

from fastapi.testclient import TestClient

from sms_wiz.api import create_app
from sms_wiz.db import connect
from sms_wiz.models import IngestRequest, Intent
from sms_wiz.pipeline import Ingestor
from sms_wiz.threats import backfill_from_events, ensure_seeds, list_threats, match_threats, record_threat


def test_seeds_and_upsert_increment_hits(tmp_path):
    conn = connect(str(tmp_path / "t.db"))
    inserted = ensure_seeds(conn)
    assert inserted >= 5
    first = record_threat(conn, "sender", "+919999900001", "echallan_scam", severity="critical")
    assert first["hit_count"] == 1
    second = record_threat(conn, "sender", "+919999900001", "echallan_scam", severity="critical")
    assert second["hit_count"] == 2
    conn.close()


def test_quarantine_auto_learns_and_flags_repeat_sender(tmp_path):
    db_file = str(tmp_path / "t.db")
    ingestor = Ingestor(db_file)
    res1 = ingestor.ingest(
        IngestRequest(
            sender="+919876543210",
            body="Alert E-Challan INR 1000 paynow: http://parivahan-echallan-pay.com/echallan.apk",
        )
    )
    assert res1.event.intent is Intent.PHISHING_SUSPECT

    conn = connect(db_file)
    threats = list_threats(conn)
    indicators = {t["indicator"] for t in threats}
    assert "+919876543210" in indicators
    assert "parivahan-echallan-pay.com" in indicators

    res2 = ingestor.ingest(
        IngestRequest(
            sender="+919876543210",
            body="Hello, please check your pending notice",
        )
    )
    assert "known_threat" in res2.event.flags
    assert any(a.kind == "known_threat" for a in res2.alerts)
    assert len(match_threats(conn, "+919876543210", "hi")) >= 1
    conn.close()


def test_threats_api_crud_and_backfill(tmp_path):
    db_file = str(tmp_path / "api.db")
    app = create_app(db_path=db_file)
    client = TestClient(app)

    r = client.get("/api/v1/threats")
    assert r.status_code == 200
    assert len(r.json()) >= 5

    created = client.post(
        "/api/v1/threats",
        json={"kind": "sender", "indicator": "AD-SPAMMY-P", "category": "ucc_spam", "severity": "medium"},
    )
    assert created.status_code == 200
    tid = created.json()["id"]

    deleted = client.delete(f"/api/v1/threats/{tid}")
    assert deleted.status_code == 200

    bf = client.post("/api/v1/threats/backfill")
    assert bf.status_code == 200
    assert "total_threats" in bf.json()
