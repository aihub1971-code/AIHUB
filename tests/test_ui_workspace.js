// Run with: node tests/test_ui_workspace.js
// Exercise the shipped workspace and Markdown renderer without a network or browser.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const staticPath = path.join(__dirname, "../app/static");
const html = fs.readFileSync(path.join(staticPath, "workspace.html"), "utf8");
const script = fs.readFileSync(path.join(staticPath, "workspace.js"), "utf8");
const markdown = fs.readFileSync(path.join(staticPath, "workspace-markdown.js"), "utf8");
const A = "geopolitical-morning", B = "education-observatory", PREFIX = "/v1/public/workspace";
const SOURCE_ID = "src_" + "a".repeat(16);
const clone = value => JSON.parse(JSON.stringify(value));
const conversationId = number => "conv_" + number.toString(16).padStart(32, "0");

class Element {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase(); this.nodeType = 1; this.childNodes = [];
    this.className = ""; this.value = ""; this.attributes = {}; this.events = {};
    this.dataset = {}; this.style = {}; this.disabled = false; this.hidden = false;
    this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 100;
    this.classList = {
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...names])].join(" "); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter(name => !names.includes(name)).join(" "); },
      contains: name => this.className.split(/\s+/).includes(name),
      toggle: (name, force) => { const add = force === undefined ? !this.classList.contains(name) : force;
        this.classList[add ? "add" : "remove"](name); return add; }
    };
  }
  get children() { return this.childNodes.filter(node => node.nodeType === 1); }
  get textContent() { return this.childNodes.map(node => node.textContent).join(""); }
  set textContent(value) { this.childNodes = [{nodeType: 3, textContent: String(value)}]; }
  set innerHTML(_) { throw new Error("Untrusted report or chat content must not be inserted as HTML."); }
  append(...nodes) { for (const node of nodes) { const child = typeof node === "string" ? {nodeType: 3, textContent: node} : node;
    child.parentNode = this; this.childNodes.push(child); } }
  replaceChildren(...nodes) { this.childNodes = []; this.append(...nodes); }
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if (name === "class") this.className = String(value);
    if (["id", "dir", "lang", "placeholder", "value", "href", "type"].includes(name)) this[name] = String(value);
    if (["disabled", "hidden", "open"].includes(name)) this[name] = true;
    if (name.startsWith("data-")) this.dataset[name.slice(5).replace(/-([a-z])/g, (_, char) => char.toUpperCase())] = String(value);
  }
  getAttribute(name) { return this.attributes[name] ?? null; }
  removeAttribute(name) { delete this.attributes[name]; if (["hidden", "disabled", "open"].includes(name)) this[name] = false; else delete this[name]; }
  querySelectorAll(selector) {
    if (/^\[[\w-]+\]$/.test(selector)) return descendants(this, el => el.getAttribute(selector.slice(1, -1)) !== null);
    if (selector.startsWith(".")) return descendants(this, el => el.classList.contains(selector.slice(1)));
    if (selector.startsWith("#")) return descendants(this, el => el.id === selector.slice(1));
    return descendants(this, el => el.tagName === selector.toUpperCase());
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  addEventListener(type, listener) { (this.events[type] ||= []).push(listener); }
  async emit(type, details = {}) {
    let prevented = false;
    const event = {target: this, key: "", shiftKey: false, isComposing: false,
      preventDefault() { prevented = true; }, ...details};
    for (const listener of this.events[type] || []) await listener(event);
    return prevented;
  }
  async click() { if (this.disabled) return; return this.emit("click"); }
  requestSubmit() { return this.emit("submit"); }
  focus() { this.ownerDocument.activeElement = this; }
  setSelectionRange(start, end) { this.selectionStart = start; this.selectionEnd = end; }
  scrollIntoView() {}
}

function descendants(root, predicate) {
  return root.children.flatMap(child => [...(predicate(child) ? [child] : []), ...descendants(child, predicate)]);
}
const byClass = (root, name) => descendants(root, element => element.classList.contains(name));
const byTag = (root, tag) => descendants(root, element => element.tagName === tag.toUpperCase());
const profiles = [A, B].map((id, i) => ({id, agent_id: id, name: i ? "Education observer" : "Geopolitical briefing",
  name_en: i ? "Education observer" : "Geopolitical briefing", name_ar: i ? "مرصد التعليم" : "الإحاطة الجيوسياسية",
  enabled: true, kind: "briefing", default_topic_en: i ? "Global education developments" : "Consequential global geopolitics",
  default_topic_ar: i ? "مستجدات التعليم العالمية" : "التطورات الجيوسياسية العالمية",
  views: [{id: "evidence", title_en: "News", title_ar: "الأخبار"}, {id: "pack", title_en: "Briefing Pack", title_ar: "ملف الإحاطة"}]}));

function job(agent = A, sequence = 1, overrides = {}) {
  return {job_id: `job_${agent}_${sequence}`, agent_id: agent, generation_seq: sequence, topic: profiles.find(p => p.id === agent).default_topic_en,
    language: "en", status: "queued", stage: "queued", research_window: {start: "2026-10-20T00:00:00Z", end: "2026-10-20T12:00:00Z"}, ...overrides};
}
function report(agent = A, sequence = 1, overrides = {}) {
  const current = job(agent, sequence), reportId = `rpt_${agent}_${sequence}`;
  return {report_id: reportId, agent_id: agent, job_id: current.job_id, generation_seq: sequence, topic: current.topic,
    language: "en", research_cutoff: current.research_window.end, research_window: current.research_window,
    pdf: {status: "ready"}, status: "ready", result: {schema_version: 2, agent_id: agent, language: "en", kind: "briefing",
      title: `Saved ${agent} generation ${sequence}`, research_window: current.research_window,
      items: [{id: "n1", headline: `Verified ${agent} headline ${sequence}`, publisher: "Fixture Publisher",
        published_at: "2026-10-20T11:00:00Z", summary: "A synthetic verified finding.", why_it_matters: "Evidence-based implications.",
        url: "https://publisher.example/synthetic", source_ids: [SOURCE_ID]}],
      sections: [{id: "executive_summary", title: "Executive summary", body: "**Synthetic assessment.** [Evidence](https://publisher.example/synthetic)", source_ids: [SOURCE_ID]}],
      sources: [{id: SOURCE_ID, title: "Original synthetic evidence", publisher: "Fixture Publisher", role: "fresh",
        url: "https://publisher.example/synthetic", published_at: "2026-10-20T11:00:00Z"}], limitations: []}, ...overrides};
}

function harness(options = {}) {
  const elements = new Map(), tree = new Element("document"), stack = [tree];
  const voidTags = new Set(["META", "LINK", "INPUT", "BR", "HR", "IMG"]);
  for (const match of html.matchAll(/<\/?([a-zA-Z][a-zA-Z0-9-]*)\b([^>]*)>|([^<]+)/g)) {
    if (match[3] !== undefined) { stack.at(-1).append(match[3]); continue; }
    if (match[0].startsWith("</")) { if (stack.length > 1) stack.pop(); continue; }
    const element = new Element(match[1]);
    for (const attribute of match[2].matchAll(/([a-zA-Z_][\w-]*)(?:="([^"]*)")?/g)) element.setAttribute(attribute[1], attribute[2] ?? "");
    if (element.id) elements.set(element.id, element);
    stack.at(-1).append(element); if (!voidTags.has(element.tagName)) stack.push(element);
  }
  const body = byTag(tree, "body")[0], requests = [], timers = new Map();
  const storage = options.storage || new Map([["aihub.language", options.language || "en"]]);
  const session = options.session || new Map();
  const storageApi = map => ({getItem: key => map.get(key) ?? null, setItem: (key, value) => map.set(key, String(value)), removeItem: key => map.delete(key)});
  const document = {body, documentElement: byTag(tree, "html")[0], activeElement: body, title: "",
    getElementById: id => elements.get(id), createElement: tag => {const el = new Element(tag); el.ownerDocument = document; return el;},
    createTextNode: text => ({nodeType: 3, textContent: String(text)}), querySelectorAll: selector => tree.querySelectorAll(selector)};
  for (const element of descendants(tree, () => true)) element.ownerDocument = document;
  const server = {jobs: new Map(), reports: new Map(), snapshots: new Map(), sequences: new Map(), conversations: 0};
  let uuid = 0, timerId = 0;
  const respond = (data, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => clone(data)});
  function defaultResponse(url, request) {
    const method = request.method || "GET", parsed = new URL(url, "https://ai-hub.test");
    const pathname = parsed.pathname, input = request.body ? JSON.parse(request.body) : {};
    if (pathname === "/v1/public/bootstrap") return respond({});
    if (pathname === PREFIX + "/config") return respond({agents: profiles, agent_count: profiles.length});
    const generate = pathname.match(/^\/v1\/public\/workspace\/agents\/([^/]+)\/reports$/);
    if (generate && method === "POST") {
      const agent = decodeURIComponent(generate[1]), sequence = (server.sequences.get(agent) || 0) + 1;
      server.sequences.set(agent, sequence);
      const queued = job(agent, sequence, {topic: input.topic, language: input.language});
      server.jobs.set(queued.job_id, {job: queued, report: null});
      return respond({job: queued, last_successful_report: null, joined_existing: false}, 202);
    }
    const agentPath = pathname.match(/^\/v1\/public\/workspace\/agents\/([^/]+)$/);
    if (agentPath) return respond(server.snapshots.get(decodeURIComponent(agentPath[1])) || {last_successful_report: null, active_job: null});
    const jobPath = pathname.match(/^\/v1\/public\/workspace\/jobs\/([^/]+)$/);
    if (jobPath) return respond(server.jobs.get(decodeURIComponent(jobPath[1])) || {detail: {code: "job_not_found"}}, server.jobs.has(decodeURIComponent(jobPath[1])) ? 200 : 404);
    if (pathname === PREFIX + "/reports") return respond({reports: [...server.reports.values()].filter(r => !parsed.searchParams.get("agent_id") || r.agent_id === parsed.searchParams.get("agent_id"))});
    const reportPath = pathname.match(/^\/v1\/public\/workspace\/reports\/([^/]+)$/);
    if (reportPath) return respond(server.reports.get(decodeURIComponent(reportPath[1])) || {detail: {code: "report_not_found"}}, server.reports.has(decodeURIComponent(reportPath[1])) ? 200 : 404);
    if (pathname === "/v1/public/chat/conversations") {
      if (method === "POST") return respond({conversation_id: conversationId(++server.conversations)}, 201);
      return respond({conversations: []});
    }
    if (/^\/v1\/public\/chat\/conversations\/[^/]+$/.test(pathname)) return respond({messages: [], pagination: {has_more: false}});
    if (/^\/v1\/public\/workspace\/chat\/conversations\/[^/]+\/messages$/.test(pathname)) return respond(answer(input));
    throw new Error("Unexpected fixture route: " + method + " " + url);
  }
  const context = {document, URL, AbortController, console,
    localStorage: storageApi(storage), sessionStorage: storageApi(session), crypto: {randomUUID: () => "workspace-fixture-" + (++uuid)},
    setTimeout: (callback, ms) => { const id = ++timerId; timers.set(id, {callback, ms}); return id; },
    clearTimeout: id => timers.delete(id),
    fetch: async (url, request) => { requests.push({url, options: request});
      const custom = options.fetch ? await options.fetch(url, request, respond, server) : undefined;
      return custom === undefined ? defaultResponse(url, request) : custom; }
  };
  context.window = context;
  const exposed = script.replace(/\n  initialize\(\);\n\}\)\(typeof window!=="undefined"\?window:globalThis\);\s*$/,
    "\n  global.AIHubWorkspaceTest={initialize,openAgent,generateReport,acceptJob,acceptReport,pollJob,openSaved,loadHistory,setLanguage,request,state,chatState,sendChat,current,selected:()=>selectedId};\n})(typeof window!==\"undefined\"?window:globalThis);");
  assert.notEqual(exposed, script, "The harness must exercise the actual initialization and shipped workflow functions.");
  vm.createContext(context); vm.runInContext(markdown, context); vm.runInContext(exposed, context);
  async function settle() { for (let i = 0; i < 60; i++) await Promise.resolve(); }
  async function advance(ms = 1800) { const pending = [...timers].filter(([, entry]) => entry.ms <= ms);
    for (const [id, entry] of pending) { if (timers.delete(id)) entry.callback(); } await settle(); }
  const generations = () => requests.filter(request => request.options.method === "POST" && /\/agents\/[^/]+\/reports$/.test(request.url));
  const messages = () => requests.filter(request => request.options.method === "POST" && /\/messages$/.test(request.url));
  return {...context.AIHubWorkspaceTest, elements, body, requests, storage, session, server, settle, advance, generations, messages};
}

function answer(input = {}, overrides = {}) {
  return {conversation_id: conversationId(1), user_message: {id: "message_user_1", role: "user", content: input.message || "Question"},
    message: {id: "message_answer_1", role: "assistant", content: "**Saved evidence answer.** [Source](https://publisher.example/synthetic)"},
    context: {report_id: input.report_id, report_ids: input.report_id ? [input.report_id] : [], agent_ids: input.agent_id ? [input.agent_id] : []}, ...overrides};
}
async function start(options = {}) { const ui = harness(options); await ui.initialize(); await ui.settle(); return ui; }
async function topicKey(ui, details = {}) { const prevented = await ui.elements.get("topic-input").emit("keydown", {key: "Enter", ...details}); await ui.settle(); return prevented; }
async function typeChat(ui, text) { const input = ui.elements.get("discussion-panel").querySelector("textarea");
  input.value = text; await input.emit("input"); return input; }
function seed(ui, agent = A, sequence = 1) { const saved = report(agent, sequence); ui.server.reports.set(saved.report_id, saved);
  ui.server.snapshots.set(agent, {last_successful_report: saved, active_job: null}); ui.server.sequences.set(agent, sequence); return saved; }

test("the public workspace has no credential, cost, admin, or upload controls", async () => {
  assert.doesNotMatch(html, /type="(?:password|file)"|id="(?:username|password)"|\/admin|dailyCost|monthlyCost|accountOverview|usage-details|loginBox/);
  assert.match(html, /workspace\.js\?v=2\.0\.2/);
  assert.doesNotMatch(html, /src="\/static\/(?:app|public)\.js/);
  const ui = await start();
  await assert.rejects(ui.request("/v1/usage"));
  await assert.rejects(ui.request("https://external.example/v1/public/bootstrap"));
  assert.equal(ui.requests.length, 2);
  for (const request of ui.requests) {
    assert.equal(request.options.credentials, "same-origin"); assert.equal(request.options.cache, "no-store");
    assert.equal(request.options.headers.Authorization, undefined);
  }
});

test("card selection automatically queues the selected profile default exactly once", async () => {
  const ui = await start();
  const cards = byClass(ui.elements.get("agent-grid"), "agent-card");
  assert.equal(cards.length, 2); await cards[0].click(); await ui.settle();
  assert.equal(ui.generations().length, 1);
  assert.deepEqual(JSON.parse(ui.generations()[0].options.body), {topic: profiles[0].default_topic_en, language: "en"});
  assert.match(ui.generations()[0].url, new RegExp(A + "/reports$"));
  assert.equal(ui.selected(), A); assert.equal(ui.elements.get("workspace-view").hidden, false);
  assert.match(ui.elements.get("job-progress").textContent, /Queued for research/);
  assert.match(ui.elements.get("job-progress").textContent, /continues even if/);
});

test("Enter uses the topic form and Shift Enter or composition does not submit", async () => {
  const ui = await start(); await ui.openAgent(A, false); await ui.settle();
  ui.elements.get("topic-input").value = "Refined geopolitical question";
  assert.equal(await topicKey(ui, {shiftKey: true}), false);
  assert.equal(await topicKey(ui, {isComposing: true}), false);
  assert.equal(ui.generations().length, 0);
  assert.equal(await topicKey(ui), true);
  assert.equal(ui.generations().length, 1);
  assert.equal(JSON.parse(ui.generations()[0].options.body).topic, "Refined geopolitical question");
  await ui.elements.get("topic-form").requestSubmit(); await ui.settle();
  assert.equal(ui.generations().length, 1, "The active matching job must reconnect rather than create another report.");
});

test("duplicate card clicks and Enter while POST is in flight cannot issue another generation", async () => {
  let release;
  const ui = await start({fetch: (url, request, respond) => {
    if (request.method === "POST" && url.endsWith("/reports")) return new Promise(resolve => { release = () => resolve(respond({job: job()}, 202)); });
  }});
  const first = byClass(ui.elements.get("agent-grid"), "agent-card")[0].click(); await ui.settle();
  assert.equal(ui.generations().length, 1); assert.equal(ui.elements.get("generate-button").disabled, true);
  await topicKey(ui); await byClass(ui.elements.get("agent-grid"), "agent-card")[0].click(); await ui.settle();
  assert.equal(ui.generations().length, 1);
  ui.server.jobs.set(job().job_id, {job: job(), report: null}); release(); await first; await ui.settle();
  await topicKey(ui); assert.equal(ui.generations().length, 1);
});

test("an unconfirmed generation retry keeps its idempotency key and hides diagnostics", async () => {
  const ui = await start({fetch: (url, request) => {
    if (request.method === "POST" && url.endsWith("/reports")) throw new Error("PRIVATE provider key and cost diagnostic");
  }});
  await ui.openAgent(A, true); await ui.settle(); await topicKey(ui);
  assert.equal(ui.generations().length, 2);
  assert.equal(ui.generations()[0].options.headers["Idempotency-Key"], ui.generations()[1].options.headers["Idempotency-Key"]);
  assert.doesNotMatch(ui.body.textContent, /PRIVATE|provider key|cost diagnostic/);
});

for (const language of ["en", "ar"]) {
  test(`an asynchronous daily report limit explains the Dubai reset in ${language} and keeps the saved report`, async () => {
    const ui = await start({language}), saved = seed(ui);
    await ui.openAgent(A, true); await ui.settle();
    const queued = ui.current().job;
    ui.server.jobs.set(queued.job_id, {job: {...queued, status: "failed", error_code: "daily_report_limit"}, report: null});
    await ui.advance();
    const notice = ui.elements.get("report-notice").textContent;
    assert.match(notice, language === "en" ? /Today's report limit has been reached/ : /تم بلوغ حدّ التقارير لهذا اليوم/);
    assert.match(notice, /00:00/); assert.match(notice, /UTC\+04:00/);
    assert.match(notice, language === "en" ? /next midnight.*Dubai/ : /منتصف الليل التالي.*دبي/);
    assert.doesNotMatch(notice, /start a new attempt|بدء محاولة جديدة|\$|USD|50|budget|cost/i);
    assert.equal(ui.current().report.report_id, saved.report_id);
    assert.equal(ui.elements.get("pdf-download").hidden, false);
    assert.equal(ui.elements.get("job-progress").hidden, true);
    await ui.advance(); await ui.advance();
    assert.equal(ui.generations().length, 1, "A blocked terminal job must not retry research automatically.");
  });
}

test("a synchronous report-limit rejection uses the same specific reset message", async () => {
  const ui = await start({fetch: (url, request, respond) => {
    if (request.method === "POST" && url.endsWith("/reports"))
      return respond({detail: {code: "daily_report_limit", message: "PRIVATE diagnostics"}}, 429);
  }});
  const saved = seed(ui); await ui.openAgent(A, true); await ui.settle();
  assert.match(ui.elements.get("report-notice").textContent, /Today's report limit.*00:00 Dubai time/);
  assert.doesNotMatch(ui.body.textContent, /PRIVATE/);
  assert.equal(ui.current().report.report_id, saved.report_id);
});

for (const [code, english, arabic] of [
  ["daily_budget_limit", /daily allowance.*next midnight/, /الاستخدام اليومي.*منتصف الليل التالي/],
  ["monthly_budget_limit", /monthly allowance.*next month/, /الاستخدام الشهري.*الشهر التالي/]
]) {
  test(`asynchronous ${code} describes the allowance without exposing costs in either language`, async () => {
    const ui = await start(); seed(ui); await ui.openAgent(A, true); await ui.settle();
    const queued = ui.current().job;
    ui.server.jobs.set(queued.job_id, {job: {...queued, status: "failed", error_code: code,
      error_message: "PRIVATE provider balance $99.99 USD"}, report: null});
    await ui.advance();
    assert.match(ui.elements.get("report-notice").textContent, english);
    await ui.elements.get("language-ar").click();
    assert.match(ui.elements.get("report-notice").textContent, arabic);
    assert.doesNotMatch(ui.body.textContent, /PRIVATE|\$|USD|99\.99|budget|cost/i);
    assert.equal(ui.generations().length, 1);
  });
}

test("late older generation completion cannot replace a newer report or current job", async () => {
  const ui = await start(); seed(ui, A, 2); await ui.openAgent(A, false); await ui.settle();
  ui.acceptJob(A, job(A, 3));
  ui.server.jobs.set(job(A, 1).job_id, {job: job(A, 1, {status: "ready", stage: "ready"}), report: report(A, 1)});
  await ui.pollJob(A, job(A, 1).job_id);
  assert.equal(ui.current().latest.generation_seq, 2); assert.equal(ui.current().report.generation_seq, 2);
  assert.equal(ui.current().job.generation_seq, 3);
  assert.match(ui.elements.get("report-meta").textContent, /generation 2/);
  assert.doesNotMatch(ui.elements.get("report-meta").textContent, /generation 1/);
  assert.match(ui.elements.get("pdf-download").href, /_2\/pdf$/);
});

test("source and pack tabs, language changes, and saved-history navigation never generate", async () => {
  const ui = await start(); const latest = seed(ui, A, 2), old = report(A, 1); ui.server.reports.set(old.report_id, old);
  await ui.openAgent(A, false); await ui.settle();
  assert.match(ui.elements.get("report-content").textContent, /Verified geopolitical-morning headline 2/);
  await ui.elements.get("tab-pack").click();
  assert.match(ui.elements.get("report-content").textContent, /Executive summary/);
  assert.equal(byTag(ui.elements.get("report-content"), "strong")[0].textContent, "Synthetic assessment.");
  assert.ok(byClass(ui.elements.get("report-content"), "source-record").length);
  await ui.elements.get("language-ar").click();
  assert.equal(ui.elements.get("tab-pack").textContent, "ملف الإحاطة");
  assert.match(ui.elements.get("report-meta").textContent, /generation 2/);
  ui.elements.get("history-menu").open = true; await ui.elements.get("history-menu").emit("toggle"); await ui.settle();
  const history = byClass(ui.elements.get("report-history"), "history-record"); assert.equal(history.length, 2);
  await history[1].click(); await ui.settle();
  assert.equal(ui.current().report.report_id, old.report_id); assert.equal(ui.current().latest.report_id, latest.report_id);
  assert.equal(ui.current().pinned, true); assert.match(ui.elements.get("report-meta").textContent, /generation 1/);
  await ui.elements.get("tab-evidence").click();
  assert.match(ui.elements.get("report-content").textContent, /headline 1/);
  assert.equal(ui.generations().length, 0);
});

test("a delayed old-agent poll updates its own state without changing the visible agent or chat", async () => {
  let release;
  const ui = await start({fetch: (url, request, respond) => {
    if (url === PREFIX + "/jobs/" + job(A, 1).job_id) return new Promise(resolve => {
      release = () => resolve(respond({job: job(A, 1, {status: "ready", stage: "ready"}), report: report(A, 1)}));
    });
  }});
  await ui.openAgent(A, true); await ui.settle();
  seed(ui, B, 2); await ui.openAgent(B, false); await ui.settle();
  const heading = ui.elements.get("workspace-heading").textContent;
  release(); await ui.settle();
  assert.equal(ui.selected(), B); assert.equal(ui.elements.get("workspace-heading").textContent, heading);
  assert.equal(ui.current().report.agent_id, B); assert.equal(ui.state(A).report.agent_id, A);
  assert.match(ui.elements.get("report-meta").textContent, /education-observatory/);
  const input = await typeChat(ui, "Explain the education finding."); await input.emit("keydown", {key: "Enter"}); await ui.settle();
  assert.equal(JSON.parse(ui.messages()[0].options.body).agent_id, B);
  assert.equal(JSON.parse(ui.messages()[0].options.body).report_id, report(B, 2).report_id);
});

test("reload reconnects to the active job and saved snapshot without regenerating", async () => {
  const session = new Map([["aihub.workspace.agent", A]]);
  const currentJob = job(A, 2), saved = report(A, 1);
  const ui = await start({session, fetch: (url, request, respond) => {
    if (url === PREFIX + "/agents/" + A) return respond({active_job: currentJob, last_successful_report: saved});
    if (url === PREFIX + "/jobs/" + currentJob.job_id) return respond({job: currentJob, report: null});
  }});
  assert.equal(ui.selected(), A); assert.equal(ui.current().job.job_id, currentJob.job_id);
  assert.equal(ui.current().report.report_id, saved.report_id); assert.equal(ui.generations().length, 0);
  assert.match(ui.elements.get("report-meta").textContent, /generation 1/);
});

test("inline chat automatically sends the active agent and report and renders the answer without upload", async () => {
  const ui = await start(); const saved = seed(ui); await ui.openAgent(A, false); await ui.settle();
  const input = await typeChat(ui, "Why does this finding matter?");
  assert.equal(await input.emit("keydown", {key: "Enter"}), true); await ui.settle();
  assert.equal(ui.messages().length, 1);
  const message = ui.messages()[0], body = JSON.parse(message.options.body);
  assert.equal(body.agent_id, A); assert.equal(body.report_id, saved.report_id); assert.equal(body.topic, saved.topic);
  assert.equal(body.message, "Why does this finding matter?"); assert.equal(message.options.headers.Authorization, undefined);
  assert.match(ui.elements.get("discussion-panel").textContent, /Saved evidence answer/);
  assert.equal(byTag(ui.elements.get("discussion-panel"), "input").length, 0);
  assert.equal(ui.generations().length, 0);
});

test("fresh chat waits for the saved report then answers with the same request key", async () => {
  const newer = report(A, 2), freshJob = job(A, 2);
  let messageRequests = 0;
  const ui = await start({fetch: (url, request, respond) => {
    if (request.method === "POST" && url.endsWith("/messages")) {
      messageRequests++;
      if (messageRequests === 1) return respond({status: "researching", job: freshJob, agent_id: A}, 202);
      const body = JSON.parse(request.body);
      return respond(answer(body, {context: {report_id: newer.report_id, report_ids: [newer.report_id],
        agent_ids: [A], fresh_research: true, research_cutoffs: [{agent_id: A, cutoff: newer.research_cutoff}]}}));
    }
  }});
  seed(ui); ui.server.jobs.set(freshJob.job_id, {job: freshJob, report: null});
  await ui.openAgent(A, false); await ui.settle();
  const input = await typeChat(ui, "What has happened since this briefing?");
  await input.emit("keydown", {key: "Enter"}); await ui.settle();
  assert.equal(ui.messages().length, 1); assert.equal(ui.chatState(A).sending, true);
  assert.match(ui.elements.get("discussion-panel").textContent, /Researching fresh evidence/);
  await ui.elements.get("discussion-panel").querySelector("textarea").emit("keydown", {key: "Enter"}); await ui.settle();
  assert.equal(ui.messages().length, 1, "While research is in flight, repeated Enter must not issue another answer request.");
  ui.server.jobs.set(freshJob.job_id, {job: {...freshJob, status: "running", stage: "preparing_pdf"}, report: {...newer, pdf: {status: "pending"}}});
  await ui.advance();
  assert.equal(ui.messages().length, 2);
  assert.equal(ui.messages()[0].options.headers["Idempotency-Key"], ui.messages()[1].options.headers["Idempotency-Key"]);
  const original = JSON.parse(ui.messages()[0].options.body), resumed = JSON.parse(ui.messages()[1].options.body);
  assert.deepEqual(resumed, {...original, fresh_job_id: freshJob.job_id});
  assert.equal(ui.chatState(A).sending, false); assert.match(ui.elements.get("discussion-panel").textContent, /Saved evidence answer/);
  assert.equal(ui.current().report.report_id, newer.report_id); assert.equal(ui.generations().length, 0);
});

test("fresh chat preserves a failed research quota code and does not retry the blocked answer", async () => {
  const freshJob = job(A, 2);
  const ui = await start({fetch: (url, request, respond) => {
    if (request.method === "POST" && url.endsWith("/messages"))
      return respond({status: "researching", job: freshJob, agent_id: A}, 202);
  }});
  const saved = seed(ui);
  ui.server.jobs.set(freshJob.job_id, {job: freshJob, report: null});
  await ui.openAgent(A, false); await ui.settle();
  const input = await typeChat(ui, "What has happened since this briefing?");
  await input.emit("keydown", {key: "Enter"}); await ui.settle();
  ui.server.jobs.set(freshJob.job_id, {job: {...freshJob, status: "failed", error_code: "daily_report_limit"}, report: null});
  await ui.advance();
  assert.equal(ui.chatState(A).sending, false);
  assert.match(ui.elements.get("discussion-panel").textContent, /Today's report limit.*00:00 Dubai time/);
  assert.doesNotMatch(ui.elements.get("discussion-panel").textContent, /start a new attempt|Check this attempt/);
  assert.equal(ui.chatState(A).attempt, null);
  assert.equal(ui.current().report.report_id, saved.report_id);
  await ui.advance(); assert.equal(ui.messages().length, 1);
  assert.equal(ui.generations().length, 0);
});

test("saved-conversation placeholder and invalid choices never request a conversation", async () => {
  const existing = conversationId(7);
  const ui = await start({language: "ar", fetch: (url, request, respond) => {
    if (url === "/v1/public/chat/conversations?limit=50") return respond({conversations: [
      {conversation_id: existing, title: "محادثة محفوظة"},
      {conversation_id: "محادثات محفوظة", title: "Malformed saved entry"}
    ]});
  }});
  seed(ui); await ui.openAgent(A, false); await ui.settle();
  const select = byClass(ui.elements.get("discussion-panel"), "chat-history-select")[0];
  const options = byTag(select, "option"), detailRequests = () => ui.requests.filter(request => /^\/v1\/public\/chat\/conversations\//.test(request.url));
  assert.equal(options[0].getAttribute("value"), "", "An option without a value submits its translated label in a real browser.");
  assert.equal(options[0].disabled, true);
  assert.equal(options[0].textContent, "محادثات محفوظة");
  assert.equal(options.length, 2, "Malformed saved conversation IDs must not become selectable.");
  for (const value of ["", "محادثات محفوظة", conversationId(8), "../admin"]) {
    select.value = value; await select.emit("change"); await ui.settle();
  }
  assert.equal(detailRequests().length, 0);
  assert.equal(ui.chatState(A).id, null);
  select.value = existing; await select.emit("change"); await ui.settle();
  assert.equal(detailRequests().length, 1);
  assert.equal(detailRequests()[0].url, "/v1/public/chat/conversations/" + existing);
  assert.equal(ui.chatState(A).id, existing);
  assert.equal(ui.session.get("aihub.workspace.chat." + A), existing);
});

test("a previously persisted translated placeholder is cleared before restoring conversation history", async () => {
  const session = new Map([["aihub.workspace.agent", A], ["aihub.workspace.chat." + A, "محادثات محفوظة"]]);
  const ui = await start({session, language: "ar"});
  assert.equal(ui.chatState(A).id, null);
  assert.equal(session.has("aihub.workspace.chat." + A), false);
  assert.equal(ui.requests.some(request => /^\/v1\/public\/chat\/conversations\//.test(request.url)), false);
  assert.equal(ui.chatState(A).error, null);
  assert.equal(ui.chatState(A).loaded, true);
});

test("malicious source markup remains literal and unsafe publisher URLs are not clickable", async () => {
  const ui = await start(), saved = seed(ui);
  saved.result.items[0].headline = "<img src=x onerror=alert(1)>";
  saved.result.items[0].url = "https://127.0.0.1/private";
  await ui.openAgent(A, false); await ui.settle();
  const content = ui.elements.get("report-content");
  assert.match(content.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(byTag(content, "img").length, 0);
  assert.equal(byTag(content, "a").some(anchor => /127\.0\.0\.1/.test(anchor.href)), false);
});

test("citation labels use stable source-list numbers while preserving internal source IDs", async () => {
  const ui = await start(), saved = seed(ui);
  const secondId = "src_" + "b".repeat(16);
  saved.result.sources.push({id: secondId, title: "Second source", publisher: "Second Publisher", role: "fresh",
    url: "https://second-publisher.example/synthetic", published_at: "2026-10-20T11:30:00Z"});
  saved.result.items[0].source_ids = [secondId, SOURCE_ID];
  await ui.openAgent(A, false); await ui.settle();
  const links = byClass(ui.elements.get("report-content"), "citation-link");
  assert.deepEqual(links.map(link => link.textContent), ["[2] Second Publisher", "[1] Fixture Publisher"]);
  assert.deepEqual(clone(ui.current().report.result.items[0].source_ids), [secondId, SOURCE_ID]);
  await ui.elements.get("tab-pack").click();
  const records = byClass(ui.elements.get("report-content"), "source-record");
  assert.match(records[0].textContent, /^\[1\] Original synthetic evidence/);
  assert.match(records[1].textContent, /^\[2\] Second source/);
  assert.doesNotMatch(ui.elements.get("report-content").textContent, /src_[a-f0-9]{16}/);
});

test("retrying a pinned older PDF preserves the newer head and active job without another report request", async () => {
  const older = report(A, 1, {pdf: {status: "failed"}}), completed = report(A, 1);
  const ui = await start({fetch: (url, request, respond) => {
    if (request.method === "POST" && url === PREFIX + "/reports/" + older.report_id + "/pdf/retry")
      return respond({job: job(A, 1, {stage: "preparing_pdf"})}, 202);
  }});
  const latest = seed(ui, A, 3); ui.server.reports.set(older.report_id, older);
  ui.server.jobs.set(job(A, 1).job_id, {job: job(A, 1, {status: "ready", stage: "ready"}), report: completed});
  await ui.openAgent(A, false); await ui.settle(); ui.acceptJob(A, job(A, 4));
  await ui.openSaved(older.report_id, A); await ui.settle();
  assert.equal(ui.elements.get("pdf-retry").hidden, false);
  await ui.elements.get("pdf-retry").click(); await ui.settle();
  assert.equal(ui.current().report.report_id, older.report_id); assert.equal(ui.current().report.pdf.status, "ready");
  assert.equal(ui.current().latest.report_id, latest.report_id); assert.equal(ui.current().job.generation_seq, 4);
  assert.equal(ui.current().pinned, true); assert.equal(ui.elements.get("pdf-download").hidden, false);
  assert.equal(ui.generations().length, 0);
  assert.equal(ui.requests.filter(request => request.options.method === "POST").length, 1);
});

test("balanced source URL parentheses remain intact in report and chat Markdown citations", async () => {
  const url = "https://fixture.example/news_(briefing)";
  const content = `See [the saved paper](${url}).`;
  const ui = await start({fetch: (path, request, respond) => {
    if (request.method === "POST" && path.endsWith("/messages"))
      return respond(answer(JSON.parse(request.body), {message: {id: "answer_parentheses", role: "assistant", content}}));
  }});
  const saved = seed(ui);
  saved.result.sections[0].body = content;
  saved.result.sources[0].url = url; saved.result.items[0].url = url;
  await ui.openAgent(A, false); await ui.settle(); await ui.elements.get("tab-pack").click();
  const reportAnchor = byTag(ui.elements.get("report-content"), "a").find(a => a.textContent === "the saved paper");
  assert.equal(reportAnchor.href, url);
  assert.equal(reportAnchor.parentNode.textContent, "See the saved paper.");
  const input = await typeChat(ui, "Explain the saved paper.");
  await input.emit("keydown", {key: "Enter"}); await ui.settle();
  const chatAnchor = byTag(ui.elements.get("discussion-panel"), "a").find(a => a.textContent === "the saved paper");
  assert.equal(chatAnchor.href, url);
  assert.equal(chatAnchor.parentNode.textContent, "See the saved paper.");
  assert.equal(chatAnchor.target, "_blank"); assert.equal(chatAnchor.rel, "noopener noreferrer");
});

test("nested and angle-bracket source URLs preserve safe links while markup stays inert", async () => {
  const ui = await start(), saved = seed(ui);
  const nested = "https://fixture.example/news_(briefing_(updated))";
  const bracketed = "https://fixture.example/paper_(appendix)?view=full";
  saved.result.sections[0].body = `**Evidence**: [nested](${nested}) and [angle](<${bracketed}>).\n\n` +
    "<img src=x onerror=alert(1)> [unsafe](javascript:alert(1)) [local](https://127.0.0.1/private) " +
    "[credentials](https://name:secret@fixture.example/private) `literal [link](https://code.example/source)`";
  await ui.openAgent(A, false); await ui.settle(); await ui.elements.get("tab-pack").click();
  const content = ui.elements.get("report-content"), links = byTag(content, "a");
  assert.equal(links.find(a => a.textContent === "nested").href, nested);
  assert.equal(links.find(a => a.textContent === "angle").href, bracketed);
  assert.equal(links.some(a => /javascript:|127\.0\.0\.1|name:secret|code\.example/.test(a.href)), false);
  assert.equal(links.some(a => a.href === "#"), false);
  assert.equal(byTag(content, "img").length, 0);
  assert.match(content.textContent, /<img src=x onerror=alert\(1\)>/);
  assert.equal(byTag(content, "strong")[0].textContent, "Evidence");
  assert.equal(byTag(content, "code")[0].textContent, "literal [link](https://code.example/source)");
});
