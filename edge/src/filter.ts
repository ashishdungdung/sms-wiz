/**
 * Pure filter-rule & sender-kind evaluator for Cloudflare Workers.
 * Mirrors `sms_wiz/rules.py` and `ios/Packages/SMSCore/Sources/SMSCore/FilterRules.swift`.
 * Zero Node-only imports; runs inside V8 isolates and Node 24 tests alike.
 */

export interface FilterConditions {
  sender_prefixes?: string[];
  sender_kinds?: string[];
  any?: string[];
  all?: string[];
  none?: string[];
  has_link?: boolean;
  min_amount?: number;
  max_amount?: number;
}

export interface FilterRule {
  id: string;
  when: FilterConditions;
  then: {
    action: string;
    sub_action: string;
    confidence: number;
    reason: string;
  };
}

export interface FilterRuleSet {
  version?: number;
  syntax?: string;
  declared_sub_actions?: string[];
  rules: FilterRule[];
}

export interface SenderKindMap {
  syntax?: string;
  default: string;
  codes: Record<string, string>;
}

export interface FilterVerdict {
  action: "allow" | "junk" | "promotion" | "transaction";
  subAction: string;
  rule_id?: string;
}

const VALID_ACTIONS = new Set(["allow", "junk", "promotion", "transaction"]);
const OPERATOR_CODES = new Set([
  "AD", "AX", "VM", "VK", "TM", "BT", "BM", "BW", "JD", "JK", "GX", "TA", "TD", "CP",
]);
const DLT_SHAPED_RE = /^(?:[A-Z]{2}-)?[A-Z0-9]{2,9}(?:-[PSTG])?$/;

export function normaliseBody(body: string): string {
  return body.trim().replace(/\s+/g, " ").toLowerCase();
}

export function containsLink(body: string): boolean {
  const lowered = body.toLowerCase();
  if (lowered.includes("http://") || lowered.includes("https://")) return true;
  return /\bwww\./i.test(body);
}

export function senderPrefix(sender: string): string | null {
  const parts = sender
    .toUpperCase()
    .split(/[-_\s]+/)
    .filter(Boolean);
  if (parts.length === 0) return null;
  const first = parts[0];
  if (parts.length >= 3 && first.length <= 3) return parts[1];
  if (parts.length === 2) {
    return OPERATOR_CODES.has(first) || first.length <= 2 ? parts[1] : first;
  }
  return first;
}

export function resolveSenderKind(sender: string, map: SenderKindMap): string {
  const unknown = map.default ?? "unknown";
  const compact = sender.replace(/[\s\-()]/g, "");
  // 1. Personal mobile: +91 / 91 / 10 digits starting with 6-9
  const digitsOnly = compact.startsWith("+") ? compact.slice(1) : compact;
  if (/^\d+$/.test(digitsOnly)) {
    if (digitsOnly.length === 10 || (digitsOnly.length === 12 && digitsOnly.startsWith("91"))) {
      const national = digitsOnly.slice(-10);
      if ("6789".includes(national[0])) return "personal_mobile";
    }
  }
  // 2. Operator shortcode: 3-6 digits
  if (compact.length >= 3 && compact.length <= 6 && /^\d+$/.test(compact)) {
    return "shortcode";
  }
  // 3. DLT header
  const trimmedUpper = sender.trim().toUpperCase();
  if (!DLT_SHAPED_RE.test(trimmedUpper)) return unknown;
  const prefix = senderPrefix(sender);
  if (!prefix) return unknown;
  return map.codes[prefix.toUpperCase()] ?? unknown;
}

function matchesConditions(
  when: FilterConditions,
  text: string,
  prefix: string | null,
  senderKind: string,
  hasLink: boolean,
  amount: number | null,
): boolean {
  if (when.sender_prefixes && when.sender_prefixes.length > 0) {
    if (!prefix || !when.sender_prefixes.some((p) => p.toUpperCase() === prefix)) {
      return false;
    }
  }
  if (when.sender_kinds && when.sender_kinds.length > 0) {
    const kindLower = senderKind.toLowerCase();
    if (!when.sender_kinds.some((k) => k.toLowerCase() === kindLower)) {
      return false;
    }
  }
  if (when.has_link === true && !hasLink) return false;
  if (when.has_link === false && hasLink) return false;
  if (amount !== null) {
    if (when.min_amount !== undefined && amount < when.min_amount) return false;
    if (when.max_amount !== undefined && amount > when.max_amount) return false;
  }
  for (const term of when.all ?? []) {
    if (!text.includes(term.toLowerCase())) return false;
  }
  for (const term of when.none ?? []) {
    if (text.includes(term.toLowerCase())) return false;
  }
  if (when.any && when.any.length > 0) {
    if (!when.any.some((term) => text.includes(term.toLowerCase()))) return false;
  }
  return true;
}

export function evaluateFilter(
  sender: string,
  body: string,
  ruleSet: FilterRuleSet,
  senderKinds: SenderKindMap,
  opts?: { hasLink?: boolean; amount?: number | null; senderKind?: string },
): FilterVerdict {
  if (!body || !body.trim()) {
    return { action: "allow", subAction: "none" };
  }
  const text = normaliseBody(body);
  const link = opts?.hasLink ?? containsLink(body);
  const prefix = senderPrefix(sender);
  const kind = opts?.senderKind ?? resolveSenderKind(sender, senderKinds);
  const amount = opts?.amount ?? null;
  const declared = new Set(ruleSet.declared_sub_actions ?? []);

  for (const rule of ruleSet.rules) {
    if (!matchesConditions(rule.when, text, prefix, kind, link, amount)) continue;
    const action = VALID_ACTIONS.has(rule.then.action)
      ? (rule.then.action as FilterVerdict["action"])
      : "allow";
    const subAction =
      declared.size === 0 || declared.has(rule.then.sub_action)
        ? rule.then.sub_action
        : "none";
    return { action, subAction, rule_id: rule.id };
  }

  return { action: "allow", subAction: "none" };
}
