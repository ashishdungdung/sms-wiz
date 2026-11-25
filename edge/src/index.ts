/**
 * Cloudflare Worker for SMS Wiz `/api/v1/filter` network deferral.
 * Evaluates the compiled `contracts/filter_rules.json` + `contracts/sender_kinds.json`
 * at the edge and always fails open (`allow`) on malformed or empty payloads.
 */

import rawRules from "../../contracts/filter_rules.json" with { type: "json" };
import rawSenderKinds from "../../contracts/sender_kinds.json" with { type: "json" };
import {
  evaluateFilter,
  type FilterRuleSet,
  type SenderKindMap,
} from "./filter.ts";

export interface Env {
  SMSWIZ_TOKEN?: string;
  APPLE_TEAM_ID?: string;
  BUNDLE_ID?: string;
}

const RULE_SET = rawRules as FilterRuleSet;
const SENDER_KINDS = rawSenderKinds as SenderKindMap;

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json; charset=utf-8" },
  });
}

function extractFilterPayload(payload: Record<string, unknown>): {
  sender: string;
  body: string;
} {
  // Accept both SMS Wiz shape ({sender, message_body}) and Apple's native
  // IdentityLookup deferral payload ({query: {sender, message: {text}}}).
  const query = (payload.query as Record<string, unknown> | undefined) ?? payload;
  const message = query.message as Record<string, unknown> | undefined;
  const sender =
    (typeof query.sender === "string" ? query.sender : undefined) ??
    (typeof payload.sender === "string" ? payload.sender : "");
  const body =
    (typeof message?.text === "string" ? message.text : undefined) ??
    (typeof payload.message_body === "string" ? payload.message_body : undefined) ??
    (typeof payload.body === "string" ? payload.body : "");
  return { sender, body };
}

export default {
  async fetch(request: Request, env: Env): Promise<Response> {
    const url = new URL(request.url);

    if (request.method === "GET" && url.pathname === "/health") {
      return jsonResponse({
        status: "ok",
        runtime: "cloudflare-worker",
        rules: RULE_SET.rules.length,
        syntax: RULE_SET.syntax ?? "sms-wiz.filter/v1",
      });
    }

    if (
      request.method === "GET" &&
      url.pathname === "/.well-known/apple-app-site-association"
    ) {
      const team = env.APPLE_TEAM_ID ?? "TEAMID1234";
      const bundle = env.BUNDLE_ID ?? "com.example.smswiz.MessageFilterExtension";
      return jsonResponse({
        messagefilter: {
          apps: [`${team}.${bundle}`],
        },
      });
    }

    if (request.method === "POST" && url.pathname === "/api/v1/filter") {
      if (env.SMSWIZ_TOKEN) {
        const token = request.headers.get("X-SMSWiz-Token") ?? "";
        if (token !== env.SMSWIZ_TOKEN) {
          return jsonResponse({ detail: "Invalid or missing token" }, 401);
        }
      }

      let payload: Record<string, unknown>;
      try {
        payload = (await request.json()) as Record<string, unknown>;
      } catch {
        // Fail open: never break Apple's filter extension on malformed JSON.
        return jsonResponse({ action: "allow", subAction: "none" });
      }

      const { sender, body } = extractFilterPayload(payload);
      const verdict = evaluateFilter(sender, body, RULE_SET, SENDER_KINDS);
      return jsonResponse({
        action: verdict.action,
        subAction: verdict.subAction,
      });
    }

    return jsonResponse({ detail: "Not found" }, 404);
  },
};
