"""The read API behind the iOS screens: analytics, cards, rules, notifications.

These tests exercise the endpoints the app actually calls, with a real ingested
corpus, so a broken join shows up here rather than on a phone.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from sms_wiz.api import create_app

DEBIT = "Rs.1,450.00 debited from A/c XX4092 on 25-02-25 to VPA KIRANAKART@axisbank (UPI Ref 505612345678). Avl Bal: Rs.5,000.00."
SWIGGY = "Rs.2,500.00 debited from A/c XX4092 on 25-02-25 to VPA SWIGGY@icici (UPI Ref 505612345679)."
STATEMENT = "Total due Rs 12,480.00 on your SBI Card XX4433. Minimum due Rs 620.00. Due date 05-03-25."
REFUND = "Rs.1,850.00 credited to A/c XX4092 on 25-02-25 by VPA AMAZON@apl. Refund for order 405-1122."
MANDATE = "Rs.119.00 will be debited from A/c XX4092 on 26-03-25 towards SPOTIFY NACH mandate."
PHISH = "Dear customer your KYC is blocked, verify at http://sbi-kyc-verify.online/apk and install app."


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path))


def ingest(client, body: str, sender: str = "AD-HDFCBK-T", when: str = "2025-03-01T10:00:00+05:30"):
    return client.post("/api/v1/ingest-sms", json={"sender": sender, "body": body, "received_at": when})


@pytest.fixture
def seeded(client):
    for body, sender in [(DEBIT, "AD-HDFCBK-T"), (SWIGGY, "AD-HDFCBK-T"), (STATEMENT, "SBICRD-T"), (REFUND, "AD-HDFCBK-T"), (MANDATE, "AD-HDFCBK-T"), (PHISH, "9876543210")]:
        assert ingest(client, body, sender).status_code == 200
    return client


class TestAnalyticsRoutes:
    def test_categories_are_a_taxonomy(self, client):
        rows = client.get("/api/v1/categories").json()
        assert len(rows) >= 19
        assert all({"slug", "label", "group", "essential"} <= set(r) for r in rows)

    def test_overview_has_every_home_screen_block(self, seeded):
        # The bank's own date wins: "... on 25-02-25" is February money even
        # though the message was delivered on 1 March.
        body = seeded.get("/api/v1/overview", params={"month": "2025-02"}).json()
        assert body["month"]["spend"] == pytest.approx(3950.0)
        assert body["month"]["refunds"] == pytest.approx(1850.0)
        assert {"weeks", "trend", "top_merchants", "upi", "cards", "categories"} <= set(body)

    def test_weekly_and_monthly_are_series(self, seeded):
        weeks = seeded.get("/api/v1/analytics/weekly", params={"weeks": 4}).json()
        months = seeded.get("/api/v1/analytics/monthly", params={"months": 3}).json()
        assert len(weeks) == 4 and len(months) == 3
        assert {w["label"] for w in weeks} and all("spend" in w for w in weeks)
        assert sum(w["spend"] for w in weeks) >= 0
        # 25-02-25 is in the window only for a series that reaches that far back.
        assert all(m["label"] for m in months)

    def test_week_count_is_bounded(self, seeded):
        assert seeded.get("/api/v1/analytics/weekly", params={"weeks": 0}).status_code == 422
        assert seeded.get("/api/v1/analytics/weekly", params={"weeks": 500}).status_code == 422

    def test_upi_view_counts_the_rail(self, seeded):
        body = seeded.get("/api/v1/analytics/upi").json()
        assert body["transactions"] == 2
        assert body["spend"] == pytest.approx(3950.0)
        assert body["average"] == pytest.approx(1975.0)

    def test_upi_view_can_be_scoped_to_a_month(self, seeded):
        assert seeded.get("/api/v1/analytics/upi", params={"month": "2024-01"}).json()["transactions"] == 0


class TestCardsAndSubscriptions:
    def test_cards_report_statement_and_payment(self, seeded):
        body = seeded.get("/api/v1/cards").json()
        card = body["cards"][0]
        assert card["last4"] == "4433"
        assert card["statement_amount"] == pytest.approx(12480.0)
        assert card["status"] in {"unpaid", "due_soon", "overdue"}
        assert body["total_outstanding"] == pytest.approx(12480.0)

    def test_subscriptions_include_the_mandate_and_its_calendar_entry(self, seeded):
        body = seeded.get("/api/v1/subscriptions").json()
        assert body["count"] >= 1
        assert any(s["name"] == "Spotify" for s in body["series"])
        calendar = seeded.get("/api/v1/subscriptions/calendar").json()
        assert isinstance(calendar, list)

    def test_calendar_window_is_validated(self, seeded):
        assert seeded.get("/api/v1/subscriptions/calendar", params={"days": 0}).status_code == 422


class TestMerchantsAndTemplates:
    def test_merchant_stats_expose_the_dictionary(self, client):
        body = client.get("/api/v1/merchants/stats").json()
        assert body["stats"]["seed"] >= 300
        assert body["categories"]

    def test_user_merchant_can_be_deleted_but_a_seed_cannot(self, client):
        client.post("/api/v1/merchants", json={"raw": "MY KIRANA", "clean": "My Kirana", "category": "Groceries & Quick Commerce"})
        assert client.get("/api/v1/merchants", params={"q": "MY KIRANA"}).json()
        assert client.delete("/api/v1/merchants/MY KIRANA").status_code == 200
        assert client.delete("/api/v1/merchants/KIRANAKART").status_code == 404

    def test_template_catalog_lists_banks(self, client):
        rows = client.get("/api/v1/templates").json()
        assert any(r["id"] == "hdfc.balance" for r in rows)
        assert all({"id", "bank", "kind"} <= set(r) for r in rows)


class TestRulesAndFilter:
    def test_rules_catalog_is_serialisable(self, client):
        body = client.get("/api/v1/rules").json()
        assert body["rules"] and {"rules", "by_action", "sub_actions_used"} <= set(body)

    def test_rule_evaluate_reports_a_match(self, client):
        body = client.post(
            "/api/v1/rules/evaluate",
            json={"sender": "AD-HDFCBK-T", "body": "Convert to Easy EMI at 0% interest. Pre-approved for you."},
        ).json()
        assert body["matched"] is True
        assert body["verdict"]["action"] == "promotion"

    def test_rule_evaluate_reports_a_clean_message(self, client):
        body = client.post("/api/v1/rules/evaluate", json={"sender": "AD-HDFCBK-T", "body": DEBIT}).json()
        assert body["matched"] is False

    def test_rule_evaluate_requires_a_body(self, client):
        assert client.post("/api/v1/rules/evaluate", json={"sender": "AD-HDFCBK-T"}).status_code == 400

    def test_filter_uses_the_rule_fast_path(self, client):
        # Apple's contract is exactly {action, subAction}: nothing else.
        body = client.post("/api/v1/filter", json={"sender": "9876543210", "message_body": PHISH}).json()
        assert set(body) == {"action", "subAction"}
        assert body["action"] == "junk"

    def test_filter_falls_back_to_the_chain_for_unmatched_messages(self, client):
        body = client.post("/api/v1/filter", json={"sender": "AD-HDFCBK-T", "message_body": DEBIT}).json()
        assert body["action"] == "transaction"

    def test_explain_returns_the_full_decision_in_dev_mode(self, client):
        response = client.post(
            "/api/v1/filter/explain",
            json={"sender": "AD-HDFCBK-T", "message_body": DEBIT},
            headers={"X-SMSWiz-Token": "dev-token"},
        )
        # No token is configured in tests, so the guard is open. With
        # SMSWIZ_API_TOKEN set, the same call without the header is 401.
        assert response.status_code == 200
        body = response.json()
        assert body["intent"] == "expense"
        assert body["routing"]["action"] == "transaction"
        assert body["template"]["template"]
        assert {"rule", "evidence", "forensics", "confidence"} <= set(body)


class TestNotificationsAndDigest:
    def test_notifications_are_delivery_decisions(self, seeded):
        rows = seeded.get("/api/v1/notifications").json()
        assert rows, "the seeded corpus produces alerts"
        assert all({"kind", "severity", "channel", "title"} <= set(r) for r in rows)
        assert all(r["channel"] in {"push", "digest", "silent"} for r in rows)

    def test_digest_has_a_headline_and_items(self, seeded):
        body = seeded.get("/api/v1/digest").json()
        assert body["headline"].startswith("This 7 days")
        assert body["count"] >= 1

    def test_digest_window_is_bounded(self, seeded):
        assert seeded.get("/api/v1/digest", params={"days": 0}).status_code == 422


class TestEmptyDatabase:
    def test_every_read_endpoint_works_with_no_data(self, client):
        for path in [
            "/api/v1/overview",
            "/api/v1/analytics/weekly",
            "/api/v1/analytics/monthly",
            "/api/v1/analytics/upi",
            "/api/v1/subscriptions",
            "/api/v1/subscriptions/calendar",
            "/api/v1/cards",
            "/api/v1/notifications",
            "/api/v1/digest",
            "/api/v1/rules",
            "/api/v1/templates",
            "/api/v1/categories",
            "/api/v1/merchants/stats",
            "/api/v1/health/llm",
        ]:
            response = client.get(path)
            assert response.status_code == 200, path
