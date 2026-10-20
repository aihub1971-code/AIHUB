// Run with: node tests/test_ui_chat.js
// Exercises the shipped chat UI, using only Node built-ins and a minimal DOM.
// All messages and requests below are local fixtures; no provider or network is called.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");
const script = fs.readFileSync(path.join(__dirname, "../app/static/chat.js"), "utf8");

class Element {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase(); this.nodeType = 1;
    this.childNodes = []; this.attributes = {}; this.events = {}; this.style = {};
    this.className = ""; this.value = ""; this.hidden = false; this.disabled = false;
    this.scrollTop = 0; this.scrollHeight = 100;
    this.classList = {
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...names])].join(" "); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter(name => !names.includes(name)).join(" "); },
      contains: name => this.className.split(/\s+/).includes(name),
      toggle: (name, force) => { const add = force === undefined ? !this.classList.contains(name) : force; this.classList[add ? "add" : "remove"](name); return add; }
    };
  }
  get children() { return this.childNodes.filter(node => node.nodeType === 1); }
  get textContent() { return this.childNodes.map(node => node.textContent).join(""); }
  set textContent(value) { this.childNodes = [{nodeType: 3, textContent: String(value)}]; }
  set innerHTML(_) { throw new Error("Chat must not insert raw HTML."); }
  get href() { return this.attributes.href || ""; }
  set href(value) { this.attributes.href = String(value); }
  append(...nodes) {
    for (const node of nodes) {
      const child = typeof node === "string" ? {nodeType: 3, textContent: node} : node;
      child.parentNode = this; this.childNodes.push(child);
    }
  }
  replaceChildren(...nodes) { this.childNodes = []; this.append(...nodes); }
  replaceWith(node) {
    if (this.parentNode) {
      const siblings = this.parentNode.childNodes;
      node.parentNode = this.parentNode;
      siblings.splice(siblings.indexOf(this), 1, node);
    }
  }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  getAttribute(name) { return this.attributes[name] ?? null; }
  querySelectorAll(selector) { return descendants(this, element => element.tagName.toLowerCase() === selector); }
  addEventListener(type, callback) { (this.events[type] ||= []).push(callback); }
  async dispatch(type, props = {}) {
    if (this.disabled && ["click", "keydown"].includes(type)) return {prevented: false};
    const event = {prevented: false, preventDefault() { this.prevented = true; }, ...props};
    for (const callback of this.events[type] || []) await callback(event);
    return event;
  }
  click() { return this.dispatch("click"); }
  focus() { this.focused = true; }
}
function descendants(root, predicate) {
  return root.children.flatMap(child => [...(predicate(child) ? [child] : []), ...descendants(child, predicate)]);
}
const byClass = (root, name) => descendants(root, element => element.classList.contains(name));
const byTag = (root, tag) => descendants(root, element => element.tagName === tag.toUpperCase());
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
const clone = value => value === undefined ? undefined : JSON.parse(JSON.stringify(value));

function conversation(id = "conv_one", overrides = {}) {
  return {conversation_id: id, title: "Fixture conversation", language: "ar", report_id: null,
    created_at: "2026-10-20T10:00:00Z", updated_at: "2026-10-20T10:00:00Z", message_count: 0,
    pending_turn_id: null, ...overrides};
}
function message(role, text, sequence, overrides = {}) {
  return {id: "msg_" + sequence, role, content: text, sequence, turn_id: "cht_" + Math.ceil(sequence / 2),
    status: "succeeded", error_code: null, report_id: null, created_at: "2026-10-20T10:01:00Z", ...overrides};
}
function success(conv, body, sequence = 1) {
  const user = message("user", body.message, sequence, {report_id: body.report_id});
  const assistant = message("assistant", "Fixture answer with context", sequence + 1, {report_id: body.report_id});
  return {conversation_id: conv.conversation_id, turn_id: user.turn_id,
    conversation: {...conv, title: body.message.slice(0, 120), report_id: body.report_id, pending_turn_id: null, message_count: sequence + 1},
    user_message: user, message: assistant, accounting: {estimated_cost_usd: 0.01, cost_is_estimate: true},
    context: {report_id: body.report_id, history_messages_included: sequence - 1, history_truncated: false}, idempotency_replayed: false};
}

function harness(customRequest, options = {}) {
  const root = new Element("div");
  const calls = [];
  let lang = "ar", currentReport = options.report || null, serial = 0, copied = "";
  const conv = conversation();
  const window = {
    AIHubI18n: {getLanguage: () => lang, t: (key, params = {}) => lang + ":" + key + (Object.keys(params).length ? ":" + JSON.stringify(params) : "")},
    navigator: {clipboard: {writeText: async text => { copied = text; }}},
    crypto: {randomUUID: () => "fixture-key-" + (++serial)},
    CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options?.detail; } },
    dispatchEvent: () => {}
  };
  const document = {createElement: tag => new Element(tag), createTextNode: text => ({nodeType: 3, textContent: String(text)})};
  const request = async (url, method = "GET", body, headers = {}) => {
    assert.match(url, /^\/v1\/chat\/conversations(?:[/?]|$)/, "Chat can only call same-origin chat routes through the callback.");
    assert.deepEqual(Object.keys(headers).filter(key => key !== "Idempotency-Key"), [], "Chat never receives or creates authorization headers.");
    const call = {url, method, body: clone(body), headers: clone(headers)};
    calls.push(call);
    if (customRequest) return customRequest(call, calls);
    if (method === "GET" && url.includes("?limit=50")) return {conversations: []};
    if (method === "POST" && url.endsWith("/conversations")) return clone(conv);
    if (method === "POST" && url.endsWith("/messages")) return success(conv, body);
    if (method === "GET") return {...conv, messages: [], pagination: {has_more: false, next_before: null}};
    throw new Error("Unexpected fixture request");
  };
  const markdown = options.renderMarkdown || ((target, text) => { const p = new Element("p"); p.textContent = text; target.append(p); });
  vm.runInNewContext(script, {window, document, URL, Uint8Array}, {filename: "chat.js"});
  const ui = window.AIHubChat.create({root, request, renderMarkdown: markdown, getLanguage: () => lang, getCurrentReport: () => currentReport});
  const get = className => byClass(root, className)[0];
  return {root, ui, calls, get, window, copied: () => copied,
    async write(text) { const input = get("chat-input"); input.value = text; await input.dispatch("input"); },
    async send(text) { if (text !== undefined) await this.write(text); return get("chat-composer").dispatch("submit"); },
    setLanguage(value) { lang = value; ui.setLanguage(); },
    setReport(value) { currentReport = value; ui.refreshReportContext(); }
  };
}

test("chat starts private, localizes its chrome, and suggestions fill a draft without sending", async () => {
  const h = harness();
  assert.equal(h.root.hidden, true);
  assert.equal(h.calls.length, 0);
  await h.ui.login();
  assert.equal(h.root.hidden, false);
  assert.equal(h.root.dir, "rtl");
  assert.equal(byClass(h.root, "chat-suggestion").length, 3);
  await byClass(h.root, "chat-suggestion")[0].click();
  assert.equal(h.get("chat-input").value, "ar:chat.suggestionQueryPrompt");
  assert.equal(h.calls.filter(call => call.method === "POST").length, 0);
  h.setLanguage("en");
  assert.equal(h.root.dir, "ltr");
  assert.equal(h.get("chat-send-button").textContent, "en:chat.send");
  assert.equal(h.get("chat-input").value, "ar:chat.suggestionQueryPrompt", "Switching UI language does not rewrite a draft.");
});

test("a first send creates a conversation, posts one key, and follow-ups keep the server conversation", async () => {
  const h = harness();
  await h.ui.login();
  await h.send("سؤال تجريبي");
  await h.send("متابعة تجريبية");
  const creations = h.calls.filter(call => call.method === "POST" && call.url.endsWith("/conversations"));
  const sends = h.calls.filter(call => call.url.endsWith("/messages"));
  assert.equal(creations.length, 1);
  assert.equal(sends.length, 2);
  assert.equal(sends[0].url, sends[1].url);
  assert.notEqual(sends[0].headers["Idempotency-Key"], sends[1].headers["Idempotency-Key"]);
  assert.deepEqual(sends[0].body, {message: "سؤال تجريبي", language: "ar", report_id: null});
  assert.equal(h.get("chat-send-button").disabled, true, "An empty composer is not sendable.");
  assert.deepEqual(Object.keys(h.window.AIHubChat), ["create"]);
});

test("real waiting shows the submitted user message, without fabricating an assistant response", async () => {
  const wait = deferred();
  let posted;
  const conv = conversation();
  const h = harness(call => {
    if (call.method === "GET") return {conversations: []};
    if (call.url.endsWith("/conversations")) return conv;
    posted = call.body; return wait.promise;
  });
  await h.ui.login();
  const sending = h.send("Private pending fixture");
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(byClass(h.root, "chat-message-user").length, 1);
  assert.equal(byClass(h.root, "chat-message-assistant").length, 0);
  assert.match(h.get("chat-live-status").textContent, /chat.sending/);
  assert.equal(h.get("chat-send-button").disabled, true);
  wait.resolve(success(conv, posted)); await sending;
  assert.equal(byClass(h.root, "chat-message-assistant").length, 1);
  assert.equal(byClass(h.root, "chat-pending").length, 0);
});

test("an ambiguous network retry preserves the exact key, language and report, then deduplicates the replay", async () => {
  const conv = conversation();
  let response, postCount = 0;
  const h = harness(call => {
    if (call.method === "GET" && call.url.includes("?limit=50")) return {conversations: []};
    if (call.method === "POST" && call.url.endsWith("/conversations")) return conv;
    if (call.method === "POST") {
      postCount++;
      if (postCount === 1) { response = success(conv, call.body); throw new TypeError("Private network diagnostic sentinel"); }
      return {...response, idempotency_replayed: true};
    }
    return {...response.conversation, messages: [response.user_message, response.message], pagination: {has_more: false, next_before: null}};
  }, {report: {id: "rpt_example", title: "Explicit fixture report"}});
  await h.ui.login();
  await h.get("chat-attach-button").click();
  await h.send("Original Arabic request");
  const first = h.calls.find(call => call.url.endsWith("/messages"));
  assert.ok(h.get("chat-retry-attempt"));
  assert.doesNotMatch(h.root.textContent, /Private network diagnostic sentinel/);
  h.setLanguage("en");
  h.setReport({id: "rpt_other", title: "Unselected other report"});
  await h.get("chat-retry-attempt").click();
  const sends = h.calls.filter(call => call.url.endsWith("/messages"));
  assert.equal(sends.length, 2);
  assert.deepEqual(sends[1].body, first.body);
  assert.equal(sends[1].headers["Idempotency-Key"], first.headers["Idempotency-Key"]);
  assert.equal(first.body.language, "ar");
  assert.equal(first.body.report_id, "rpt_example");
  assert.equal(byClass(h.root, "chat-message-user").length, 1);
  assert.equal(byClass(h.root, "chat-message-assistant").length, 1);
});

test("report attachment is explicit, survives a follow-up, and clearing sends an explicit null", async () => {
  const h = harness(undefined, {report: {id: "rpt_fixture", title: "Fixture report title"}});
  await h.ui.login();
  await h.send("No report selected yet");
  assert.equal(h.calls.find(call => call.url.endsWith("/messages")).body.report_id, null);
  await h.get("chat-attach-button").click();
  assert.match(h.get("chat-context-chip").textContent, /Fixture report title/);
  await h.send("Discuss my selected report");
  h.setReport({id: "rpt_not_selected", title: "Unrelated displayed report"});
  await h.send("Follow up using the previous report");
  const selected = h.calls.filter(call => call.url.endsWith("/messages"));
  assert.equal(selected[1].body.report_id, "rpt_fixture");
  assert.equal(selected[2].body.report_id, "rpt_fixture");
  await h.get("chat-remove-report").click();
  assert.match(h.get("chat-context-area").textContent, /chat.reportCleared/);
  await h.send("Continue without a report");
  assert.equal(h.calls.filter(call => call.url.endsWith("/messages")).at(-1).body.report_id, null);
});

test("saved pending and failed turns remain honest; refresh is read-only and resending is explicit", async () => {
  const conv = conversation("conv_saved", {pending_turn_id: "cht_pending", report_id: "rpt_saved"});
  let status = "pending", sequence = 3;
  const h = harness(call => {
    if (call.method === "GET" && call.url.includes("?limit=50")) return {conversations: [conv]};
    if (call.method === "GET") return {...conv, pending_turn_id: status === "pending" ? "cht_pending" : null,
      messages: [message("user", "Persisted real user text", 1, {turn_id: "cht_pending", status, report_id: "rpt_saved"})], pagination: {has_more: false}};
    return success(conv, call.body, sequence++);
  });
  await h.ui.login();
  await h.get("chat-history-item").click();
  assert.equal(byClass(h.root, "chat-message-assistant").length, 0);
  assert.match(h.get("chat-live-status").textContent, /chat.pending/);
  assert.equal(h.get("chat-send-button").disabled, true);
  status = "failed";
  await h.get("chat-refresh").click();
  assert.equal(h.calls.filter(call => call.method === "POST").length, 0);
  assert.ok(h.get("chat-send-again"));
  assert.match(h.root.textContent, /chat.newAttemptHint/);
  await h.get("chat-send-again").click();
  const sends = h.calls.filter(call => call.method === "POST");
  assert.equal(sends.length, 1);
  assert.equal(sends[0].body.report_id, "rpt_saved");
  assert.equal(sends[0].body.message, "Persisted real user text");
});

test("logout clears private DOM and rejects a late reply from the previous session", async () => {
  const wait = deferred(), conv = conversation();
  let posted;
  const h = harness(call => {
    if (call.method === "GET") return {conversations: []};
    if (call.url.endsWith("/conversations")) return conv;
    posted = call.body; return wait.promise;
  }, {report: {id: "rpt_private", title: "Private report title"}});
  await h.ui.login();
  await h.get("chat-attach-button").click();
  const pending = h.send("Secret fixture message");
  await new Promise(resolve => setImmediate(resolve));
  h.ui.logout();
  assert.equal(h.root.hidden, true);
  assert.doesNotMatch(h.root.textContent, /Secret fixture message|Private report title|Fixture conversation/);
  wait.resolve({...success(conv, posted), message: message("assistant", "Late secret response", 2)});
  await pending;
  assert.equal(h.root.hidden, true);
  assert.doesNotMatch(h.root.textContent, /Late secret response|Secret fixture message/);
  await h.ui.login();
  assert.equal(byClass(h.root, "chat-message").length, 0);
  assert.equal(h.get("chat-input").value, "");
});

test("message text stays literal, Markdown links are hardened, and copy uses the original assistant text", async () => {
  const malicious = '<img src=x onerror="alert(1)">';
  const markdown = (target, text) => {
    const paragraph = new Element("p"); paragraph.textContent = text; target.append(paragraph);
    for (const url of ["https://ft.com/content/fixture", "javascript:alert(1)", "https://user:secret@ft.com/path", "//ft.com/path", "https://ft.com/\npath"]) {
      const a = new Element("a"); a.href = url; a.textContent = "Fixture link"; target.append(a);
    }
  };
  const h = harness(undefined, {renderMarkdown: markdown});
  await h.ui.login(); await h.send(malicious);
  assert.equal(byTag(h.root, "img").length, 0);
  assert.equal(byTag(h.root, "script").length, 0);
  const links = byTag(h.root, "a");
  assert.equal(links.length, 1);
  assert.equal(links[0].href, "https://ft.com/content/fixture");
  assert.equal(links[0].target, "_blank");
  assert.equal(links[0].rel, "noopener noreferrer");
  await h.get("chat-copy-button").click();
  assert.equal(h.copied(), "Fixture answer with context");
});

test("Enter sends, Shift+Enter and IME input do not, and oversized drafts never reach the server", async () => {
  const h = harness(); await h.ui.login();
  await h.write("Keyboard fixture");
  const input = h.get("chat-input");
  const shifted = await input.dispatch("keydown", {key: "Enter", shiftKey: true});
  const composing = await input.dispatch("keydown", {key: "Enter", isComposing: true});
  assert.equal(shifted.prevented, false);
  assert.equal(composing.prevented, false);
  assert.equal(h.calls.filter(call => call.method === "POST").length, 0);
  const plain = await input.dispatch("keydown", {key: "Enter", shiftKey: false, isComposing: false});
  assert.equal(plain.prevented, true);
  assert.equal(h.calls.filter(call => call.url.endsWith("/messages")).length, 1);
  await h.send("x".repeat(8001));
  assert.equal(h.calls.filter(call => call.url.endsWith("/messages")).length, 1);
  assert.match(h.get("chat-notice").textContent, /chat.tooLong/);
});

test("an unconfigured or budget-rejected send restores its draft without automatic retry or raw diagnostics", async () => {
  const conv = conversation();
  const h = harness(call => {
    if (call.method === "GET" && call.url.includes("?limit=50")) return {conversations: []};
    if (call.method === "POST" && call.url.endsWith("/conversations")) return conv;
    if (call.method === "POST") throw Object.assign(new Error("Do not leak private provider body"), {status: 429, code: "daily_budget_exceeded", detail: {code: "daily_budget_exceeded"}});
    return {...conv, messages: [], pagination: {has_more: false}};
  });
  await h.ui.login(); await h.send("Retry this draft later");
  assert.equal(h.get("chat-input").value, "Retry this draft later");
  assert.equal(h.calls.filter(call => call.url.endsWith("/messages")).length, 1);
  assert.equal(byClass(h.root, "chat-retry-attempt").length, 0);
  assert.equal(byClass(h.root, "chat-message-assistant").length, 0);
  assert.match(h.get("chat-notice").textContent, /chat.errorBudget/);
  assert.doesNotMatch(h.root.textContent, /private provider body/);
});

test("older message pages prepend chronologically and keep loaded messages", async () => {
  const conv = conversation("conv_archive");
  const h = harness(call => {
    if (call.url.includes("?limit=50")) return {conversations: [conv]};
    if (call.url.includes("&before=3")) return {...conv, messages: [message("user", "Older question", 1), message("assistant", "Older answer", 2)], pagination: {has_more: false, next_before: null}};
    return {...conv, messages: [message("user", "Newer question", 3), message("assistant", "Newer answer", 4)], pagination: {has_more: true, next_before: 3}};
  });
  await h.ui.login(); await h.get("chat-history-item").click();
  assert.equal(h.get("chat-load-earlier").hidden, false);
  await h.get("chat-load-earlier").click();
  const content = byClass(h.root, "chat-message-content").map(node => node.textContent);
  assert.deepEqual(content, ["Older question", "Older answer", "Newer question", "Newer answer"]);
  assert.equal(h.get("chat-load-earlier").hidden, true);
});

test("a preflight history conflict or unavailable report restores the editable draft", async () => {
  for (const code of ["chat_history_changed", "chat_report_unavailable"]) {
    const conv = conversation();
    const h = harness(call => {
      if (call.method === "GET" && call.url.includes("?limit=50")) return {conversations: []};
      if (call.method === "POST" && call.url.endsWith("/conversations")) return conv;
      if (call.method === "POST") throw Object.assign(new Error("Sanitized conflict"), {status: 409, code, detail: {code}});
      return {...conv, messages: [], pagination: {has_more: false}};
    }, {report: {id: "rpt_fixture", title: "A report"}});
    await h.ui.login(); await h.get("chat-attach-button").click(); await h.send("Editable fixture draft");
    assert.equal(h.get("chat-input").value, "Editable fixture draft");
    assert.equal(h.get("chat-send-button").disabled, false);
    assert.equal(h.get("chat-remove-report").disabled, false);
    assert.equal(byClass(h.root, "chat-retry-attempt").length, 0);
    assert.equal(h.calls.filter(call => call.url.endsWith("/messages")).length, 1);
  }
});

test("an unauthorized history refresh hides and clears previously visible private content", async () => {
  const conv = conversation();
  let expired = false;
  const h = harness(call => {
    if (expired) throw Object.assign(new Error("Auth expired"), {status: 401});
    if (call.url.includes("?limit=50")) return {conversations: [conv]};
    return {...conv, messages: [message("user", "Private saved fixture", 1)], pagination: {has_more: false}};
  });
  await h.ui.login(); await h.get("chat-history-item").click();
  assert.match(h.root.textContent, /Private saved fixture/);
  expired = true; await h.get("chat-refresh").click();
  assert.equal(h.root.hidden, true);
  assert.doesNotMatch(h.root.textContent, /Private saved fixture/);
});
