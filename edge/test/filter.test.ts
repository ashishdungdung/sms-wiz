import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { evaluateFilter, type FilterRuleSet, type SenderKindMap } from "../src/filter.ts";
import worker from "../src/index.ts";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const root = path.resolve(__dirname, "../..");

const ruleSet = JSON.parse(
  fs.readFileSync(path.join(root, "contracts/filter_rules.json"), "utf8"),
) as FilterRuleSet;
const senderKinds = JSON.parse(
  fs.readFileSync(path.join(root, "contracts/sender_kinds.json"), "utf8"),
) as SenderKindMap;
const fixture = JSON.parse(
  fs.readFileSync(path.join(root, "tests/fixtures/filter_rules/cases.json"), "utf8"),
) as {
  cases: Array<{
    name: string;
    sender: string;
    body: string;
    sender_kind: string;
    expect: { action: string; sub_action: string; rule_id?: string };
  }>;
};

test("every filter_rules fixture case matches Python & Swift", () => {
  for (const c of fixture.cases) {
    const verdict = evaluateFilter(c.sender, c.body, ruleSet, senderKinds, {
      senderKind: c.sender_kind,
    });
    assert.equal(verdict.action, c.expect.action, `action mismatch for ${c.name}`);
    assert.equal(verdict.subAction, c.expect.sub_action, `subAction mismatch for ${c.name}`);
  }
});

test("worker /health returns rule count", async () => {
  const res = await worker.fetch(new Request("https://filter.example.com/health"), {});
  assert.equal(res.status, 200);
  const json = (await res.json()) as { status: string; rules: number };
  assert.equal(json.status, "ok");
  assert.equal(json.rules, ruleSet.rules.length);
});

test("worker /api/v1/filter enforces optional token and fails open on bad JSON", async () => {
  const unauth = await worker.fetch(
    new Request("https://filter.example.com/api/v1/filter", {
      method: "POST",
      body: JSON.stringify({ sender: "AD-HDFCBK-T", message_body: "Rs 500 debited" }),
    }),
    { SMSWIZ_TOKEN: "secret" },
  );
  assert.equal(unauth.status, 401);

  const badJson = await worker.fetch(
    new Request("https://filter.example.com/api/v1/filter", {
      method: "POST",
      body: "{not-json",
      headers: { "X-SMSWiz-Token": "secret" },
    }),
    { SMSWIZ_TOKEN: "secret" },
  );
  assert.equal(badJson.status, 200);
  const body = (await badJson.json()) as { action: string; subAction: string };
  assert.equal(body.action, "allow");
  assert.equal(body.subAction, "none");
});
