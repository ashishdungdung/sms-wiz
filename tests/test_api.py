from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sms_wiz.api import create_app
from sms_wiz.registry import DECLARED_PROMOTIONAL, DECLARED_TRANSACTIONAL

BANK_DEBIT = (
    "Rs.1,450.00 debited from A/c XX4092 on 25-02-25 to VPA KIRANAKART@axisbank "
    "(UPI Ref 505612345678). Avl Bal: Rs.5,000.00."
)


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path))


def post_sms(client, sender="AD-HDFCBK-T", body=BANK_DEBIT, **kwargs):
    payload = {"sender": sender, "body": body, "received_at": "2025-03-01T10:00:00+05:30"}
    payload.update(kwargs)
    return client.post("/api/v1/ingest-sms", json=payload)


class TestIngest:
    def test_health(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert body["llm_enabled"] is False
        assert body["merchant_keys"] > 20

    def test_ingest_and_read_back(self, client):
        response = post_sms(client)
        assert response.status_code == 200
        body = response.json()
        assert body["event"]["intent"] == "expense"
        assert body["event"]["ledger"]["counts_toward_spend"] is True

        events = client.get("/api/v1/events").json()
        assert len(events) == 1
        detail = client.get(f"/api/v1/events/{body['event']['event_id']}").json()
        assert detail["event_id"] == body["event"]["event_id"]

    def test_summary(self, client):
        post_sms(client)
        # The message says "on 25-02-25" and was delivered on 1 March. Money
        # buckets by the bank's date, so this is February's spending.
        summary = client.get("/api/v1/summary", params={"month": "2025-02"}).json()
        assert summary["spend_total"] == 1450.0
        assert summary["by_category"]["Groceries & Quick Commerce"] == 1450.0
        assert client.get("/api/v1/summary", params={"month": "2025-03"}).json()["spend_total"] == 0.0

    def test_summary_validates_month(self, client):
        assert client.get("/api/v1/summary", params={"month": "March"}).status_code == 422

    def test_missing_event_is_404(self, client):
        assert client.get("/api/v1/events/evt_nope").status_code == 404

    def test_cleanup_queues_group_by_folder(self, client):
        post_sms(client)
        post_sms(
            client,
            sender="AD-AMAZON-S",
            body="Your Amazon delivery OTP is 8492.",
        )
        queues = client.get("/api/v1/cleanup-queues").json()["queues"]
        folders = {q["folder"] for q in queues}
        assert "transaction/transactionalFinance" in folders
        assert "transaction/transactionalOrders" in folders


class TestFilterEndpoint:
    """Apple's network deferral contract: only action + subAction."""

    def test_flat_payload(self, client):
        response = client.post("/api/v1/filter", json={"sender": "AD-HDFCBK-T", "messageBody": BANK_DEBIT})
        assert response.status_code == 200
        body = response.json()
        assert body == {"action": "transaction", "subAction": "transactionalFinance"}

    def test_nested_payload(self, client):
        response = client.post(
            "/api/v1/filter",
            json={"version": 1, "queryRequest": {"sender": "9876543210", "messageBody": "Win a free iPhone now!"}},
        )
        assert response.json()["action"] in {"promotion", "junk", "allow", "transaction"}

    def test_returns_only_declared_subactions(self, client):
        response = client.post(
            "/api/v1/filter",
            json={"sender": "JD-ECHALN-G", "messageBody": "E-Challan of Rs 1,000 for KA01MJ1234 is pending."},
        )
        body = response.json()
        assert body["subAction"] in {s.value for s in (*DECLARED_TRANSACTIONAL, *DECLARED_PROMOTIONAL)}

    def test_junk_is_reserved_for_quarantine(self, client):
        benign = client.post(
            "/api/v1/filter", json={"sender": "AD-MYNTRA-P", "messageBody": "Flat 50% off this weekend."}
        ).json()
        assert benign["action"] == "promotion"

        scam = client.post(
            "/api/v1/filter",
            json={
                "sender": "9876543210",
                "messageBody": "Your SBI account will be blocked today. Verify at http://sbi-kyc.xyz/login",
            },
        ).json()
        assert scam["action"] == "junk"

    def test_missing_body_is_rejected(self, client):
        assert client.post("/api/v1/filter", json={"sender": "123"}).status_code == 400

    def test_capabilities_are_within_apple_limits(self, client):
        caps = client.get("/api/v1/apple/capabilities").json()
        assert len(caps["transactional"]) <= 5
        assert len(caps["promotional"]) <= 5


class TestMerchantCorrection:
    def test_user_correction_beats_seed(self, client):
        body = "Rs 320.00 spent at SAI TEJA STORES on your card."
        first = post_sms(client, body=body)
        assert first.json()["event"]["merchant"]["source"] == "none"

        client.post("/api/v1/merchants", json={"raw": "SAI TEJA STORES", "clean": "Sai Teja Kirana", "category": "Groceries & Quick Commerce"})
        second = post_sms(client, body=body.replace("320", "321"))
        merchant = second.json()["event"]["merchant"]
        assert merchant["clean"] == "Sai Teja Kirana"
        assert merchant["source"] == "user"


class TestAuth:
    def test_token_is_enforced_when_configured(self, client, monkeypatch):
        monkeypatch.setenv("SMSWIZ_TOKEN", "s3cret")
        assert post_sms(client).status_code == 401
        ok = client.post(
            "/api/v1/ingest-sms",
            json={"sender": "AD-HDFCBK-T", "body": BANK_DEBIT},
            headers={"X-SMSWiz-Token": "s3cret"},
        )
        assert ok.status_code == 200

    def test_no_token_configured_means_open_local_dev(self, client):
        assert post_sms(client).status_code == 200


class TestLanguageSurface:
    """A Hindi message must stay visibly Hindi after it is stored."""

    def test_event_list_reports_the_script(self, client):
        hindi = (
            "प्रिय ग्राहक, आपके खाते XX1094 से रु २,५००.०० को SWIGGY को डेबिट किए गए हैं "
            "(UPI Ref ५१२३४५६७८९०१). उपलब्ध शेष: रु ८,२००.००"
        )
        assert post_sms(client, sender="VM-SBIN-T", body=hindi).status_code == 200

        rows = client.get("/api/v1/events").json()
        assert len(rows) == 1
        assert rows[0]["scripts"] == ["devanagari"]
        assert rows[0]["multilingual"] is True
        assert rows[0]["amount_value"] == 2500.0
        assert rows[0]["merchant_clean"] == "Swiggy"

    def test_english_events_report_no_script(self, client):
        post_sms(client)
        rows = client.get("/api/v1/events").json()
        assert rows[0]["scripts"] == []
        assert rows[0]["multilingual"] is False
