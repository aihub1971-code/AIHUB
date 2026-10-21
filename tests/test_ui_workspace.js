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
  const body = byTag(tree, "body")[0]; if(options.owner)body.dataset.workspaceMode="owner";
  const requests = [], timers = new Map();
  const storage = options.storage || new Map([["aihub.language", options.language || "en"]]);
  const session = options.session || new Map();
  const storageApi = map => ({getItem: key => map.get(key) ?? null, setItem: (key, value) => map.set(key, String(value)), removeItem: key => map.delete(key)});
  const documentEvents = new Map();
  const document = {body, documentElement: byTag(tree, "html")[0], activeElement: body, title: "", hidden: false,
    addEventListener: (name, handler) => documentEvents.set(name, handler),
    getElementById: id => elements.get(id), createElement: tag => {const el = new Element(tag); el.ownerDocument = document; return el;},
    createTextNode: text => ({nodeType: 3, textContent: String(text)}), querySelectorAll: selector => tree.querySelectorAll(selector)};
  for (const element of descendants(tree, () => true)) element.ownerDocument = document;
  const server = options.server || {threads: new Map(), memory: [], jobs: new Map(), reports: new Map(), snapshots: new Map(), sequences: new Map(), feeds: new Map(options.feeds || []), conversations: 0};
  let uuid = 0, timerId = 0;
  const respond = (data, status = 200) => ({ok: status >= 200 && status < 300, status, json: async () => clone(data)});
  function defaultResponse(url, request) {
    const method = request.method || "GET", parsed = new URL(url, "https://ai-hub.test");
    const pathname = parsed.pathname === "/v1/workspace/bootstrap" ? "/v1/public/bootstrap" : parsed.pathname.replace(/^\/v1\/workspace\//,"/v1/public/workspace/"), input = request.body ? JSON.parse(request.body) : {};
    if (pathname === "/v1/public/bootstrap") return respond({});
    if (pathname === PREFIX + "/config") return respond({agents: options.profiles || profiles, agent_count: (options.profiles || profiles).length, news_enabled_agents: options.newsAgents || []});
    const newsPath = pathname.match(/^\/v1\/public\/workspace\/agents\/([^/]+)\/news(?:\/refresh)?$/);
    if (newsPath) return respond(server.feeds.get(decodeURIComponent(newsPath[1]) + ":" + (input.language || parsed.searchParams.get("language"))) || {status: "empty", items: [], last_successful_refresh_at: new Date(now).toISOString()});
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
    const threadPath = pathname.match(/^\/v1\/public\/workspace\/agents\/([^/]+)\/conversation$/);
    if (threadPath) {
      const agent = decodeURIComponent(threadPath[1]);
      if (!server.threads.has(agent)) server.threads.set(agent, {conversation_id: conversationId(++server.conversations), agent_id: agent, messages: [], pagination: {has_more: false}});
      return respond(server.threads.get(agent));
    }
    const conversationPath = pathname.match(/^\/v1\/public\/workspace\/conversations\/([^/]+)(?:\/messages\/stream)?$/);
    if (conversationPath) {
      const thread = [...server.threads.values()].find(item => item.conversation_id === conversationPath[1]);
      if (!thread) return respond({detail: {code: "conversation_not_found"}}, 404);
      if (method === "POST") {
        const response = answer(input, {conversation_id: thread.conversation_id,
          user_message: {id: "user_" + thread.messages.length, role: "user", content: input.message},
          message: {id: "answer_" + thread.messages.length, role: "assistant", content: "**Saved evidence answer.** [Source](https://publisher.example/synthetic)"}});
        thread.messages.push(response.user_message, response.message); return respond(response);
      }
      return respond(thread);
    }
    if (pathname === PREFIX + "/memory") {
      if (method === "POST") server.memory.push({memory_id: "memory_" + (server.memory.length + 1), ...input, status: "proposed"});
      return respond({items: server.memory.filter(item => item.status !== "rejected")});
    }
    const memoryPath = pathname.match(/^\/v1\/public\/workspace\/memory\/([^/]+)$/);
    if (memoryPath && method === "PATCH") {
      const item = server.memory.find(item => item.memory_id === memoryPath[1]);
      if (item) item.status = input.action === "approve" ? "approved" : "rejected";
      return respond(item);
    }
    throw new Error("Unexpected fixture route: " + method + " " + url);
  }
  let now = options.now ?? Date.now();
  class ClockDate extends Date { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return now; } }
  const context = {document, URL, AbortController, TextDecoder, Uint8Array, console, Date: ClockDate,
    localStorage: storageApi(storage), sessionStorage: storageApi(session), crypto: {randomUUID: () => "workspace-fixture-" + (++uuid)},
    setTimeout: (callback, ms) => { const id = ++timerId; timers.set(id, {callback, ms}); return id; },
    clearTimeout: id => timers.delete(id),
    fetch: async (url, request) => { requests.push({url, options: request});
      const custom = options.fetch ? await options.fetch(url, request, respond, server) : undefined;
      return custom === undefined ? defaultResponse(url, request) : custom; }
  };
  context.window = context;
  const exposed = script.replace(/\n  initialize\(\);\n\}\)\(typeof window!=="undefined"\?window:globalThis\);\s*$/,
    "\n  global.AIHubWorkspaceTest={initialize,openAgent,generateReport,acceptJob,acceptReport,pollJob,openSaved,loadHistory,setLanguage,request,streamAnswer,state,chatState,sendChat,loadConversation,current,loadNews,newsState,loadMemory,proposeMemory,selected:()=>selectedId};\n})(typeof window!==\"undefined\"?window:globalThis);");
  assert.notEqual(exposed, script, "The harness must exercise the actual initialization and shipped workflow functions.");
  vm.createContext(context); vm.runInContext(markdown, context); vm.runInContext(exposed, context);
  async function settle() { for (let i = 0; i < 60; i++) await Promise.resolve(); }
  async function advance(ms = 1800) { const pending = [...timers].filter(([, entry]) => entry.ms <= ms);
    for (const [id, entry] of pending) { if (timers.delete(id)) entry.callback(); } await settle(); }
  const generations = () => requests.filter(request => request.options.method === "POST" && /\/agents\/[^/]+\/reports$/.test(request.url));
  const messages = () => requests.filter(request => request.options.method === "POST" && /\/messages\/stream$/.test(request.url));
  const newsRequests = () => requests.filter(request => /\/news(?:\/refresh|\?language=)/.test(request.url));
  const visibility = async hidden => {document.hidden = hidden; documentEvents.get("visibilitychange")?.(); await settle();};
  return {...context.AIHubWorkspaceTest, elements, body, requests, storage, session, server, settle, advance, generations, messages, newsRequests, visibility, setNow: value => {now = value;}};
}

function answer(input = {}, overrides = {}) {
  return {conversation_id: conversationId(1), user_message: {id: "message_user_1", role: "user", content: input.message || "Question"},
    message: {id: "message_answer_1", role: "assistant", content: "**Saved evidence answer.** [Source](https://publisher.example/synthetic)"},
    context: {report_id: input.report_id, report_ids: input.report_id ? [input.report_id] : [], agent_ids: input.agent_id ? [input.agent_id] : []}, ...overrides};
}
async function start(options = {}) { const ui = harness(options); await ui.initialize(); await ui.settle(); return ui; }
async function openResearch(ui) {const tools = ui.elements.get("research-tools"); tools.open = true; await tools.emit("toggle"); await ui.settle();}
async function startNews(options = {}) {const ui = await start(options); await ui.openAgent((options.newsAgents || [A])[0]); await openResearch(ui); return ui;}
async function topicKey(ui, details = {}) { const prevented = await ui.elements.get("topic-input").emit("keydown", {key: "Enter", ...details}); await ui.settle(); return prevented; }
async function typeChat(ui, text) { const input = ui.elements.get("discussion-panel").querySelector("textarea");
  input.value = text; await input.emit("input"); return input; }
function seed(ui, agent = A, sequence = 1) { const saved = report(agent, sequence); ui.server.reports.set(saved.report_id, saved);
  ui.server.snapshots.set(agent, {last_successful_report: saved, active_job: null}); ui.server.sequences.set(agent, sequence); return saved; }

const NEWS_NOW = Date.parse("2026-10-20T12:00:00Z");
function newsItem(index = 1, overrides = {}) {return {article_id: "article_" + index, headline: "Live article " + index,
  summary: "Verified source metadata.", publisher: "Publisher " + (index % 3), url: "https://publisher.example/live/" + index,
  published_at: new Date(NEWS_NOW - index * 1000).toISOString(), original_language: "en", display_language: "en", ...overrides};}
function newsFeed(overrides = {}) {return {status: "fresh", items: [newsItem()], stale: false, partial: false,
  last_successful_refresh_at: "2026-10-20T11:59:00Z", next_refresh_at: "2026-10-20T12:15:00Z", pagination: {raw_count: 1}, ...overrides};}

test("agent research tools read saved news independently of report generation and shows honest refresh metadata", async () => {
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", newsFeed()]]});
  const home = ui.elements.get("report-content");
  assert.match(home.textContent, /Fresh.*Last successful refresh/);
  assert.match(home.textContent, /Manual refresh.*Opening this page displays saved news/);
  assert.doesNotMatch(home.textContent, /Next automatic refresh|Auto-refresh/);
  assert.match(home.textContent, /1 articles from the last 12 hours.*1 provider records fetched/);
  assert.equal(ui.newsRequests().length, 1); assert.equal(ui.newsRequests()[0].options.method, "GET");
  assert.equal(ui.generations().length, 0); assert.equal(ui.messages().length, 0);
  assert.equal(byTag(home, "time")[0].getAttribute("datetime"), newsItem().published_at);
  assert.equal(byTag(home, "a").find(a => /Open original source/.test(a.textContent)).href, newsItem().url);
});

test("the live news tab displays up to50 fresh unique articles and rejects old, future, undated and unsafe items", async () => {
  const items = Array.from({length: 104}, (_, i) => newsItem(i + 1));
  items.unshift(newsItem(1), newsItem(201, {published_at: "2026-10-19T23:59:59Z"}), newsItem(202, {published_at: "2026-10-20T12:00:01Z"}),
    newsItem(203, {published_at: "2026-10-20"}), newsItem(204, {url: "javascript:alert(1)"}));
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", newsFeed({items, pagination: {raw_count: items.length}})]]});
  const content = ui.elements.get("report-content"), shown = byClass(content, "live-news-item");
  assert.equal(shown.length, 50); assert.match(content.textContent, /50 articles from the last 12 hours/);
  assert.doesNotMatch(content.textContent, /Live article 20[1-4]/);
  assert.equal(ui.generations().length, 0, "Viewing the full news feed must not start a report/model request.");
});

test("manual refresh preserves cached articles during loading and provider failure is distinct from empty", async () => {
  let release;
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", newsFeed()]], fetch: (url, request, respond) => {
    if (url.endsWith("/news/refresh")) return new Promise(resolve => { release = () => resolve(respond({detail: {code: "newsdata_timeout", retryable: true, reference: "news_timeout_1"}}, 504)); });
  }});
  const first = byClass(ui.elements.get("report-content"), "news-refresh")[0].click(); await ui.settle();
  let home = ui.elements.get("report-content");
  assert.match(home.textContent, /Refreshing news/); assert.match(home.textContent, /Live article 1/);
  assert.equal(byClass(home, "news-refresh")[0].disabled, true);
  await byClass(home, "news-refresh")[0].click();
  assert.equal(ui.newsRequests().filter(r => r.options.method === "POST").length, 1);
  release(); await first; await ui.settle(); home = ui.elements.get("report-content");
  assert.match(home.textContent, /News refresh failed.*timed out/);
  assert.match(home.textContent, /Live article 1/); assert.doesNotMatch(home.textContent, /The source check completed without/);
  assert.match(home.textContent, /news_timeout_1/); assert.equal(ui.generations().length, 0);
});

test("an empty saved cache invites an explicit refresh without claiming a source search occurred", async () => {
  const cached = newsFeed({status: "empty", items: [], snapshot_id: null, last_successful_refresh_at: null});
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", cached], [A + ":ar", cached]]});
  let home = ui.elements.get("report-content");
  assert.match(home.textContent, /No saved news yet.*Select Refresh news to retrieve articles/);
  assert.doesNotMatch(home.textContent, /source check completed|Refreshing news/);
  assert.equal(byClass(home, "news-refresh")[0].disabled, false);
  ui.setNow(NEWS_NOW + 60 * 60000); await ui.advance(60 * 60000);
  assert.equal(ui.newsRequests().length, 1, "An empty cache must not cause a timed request.");
  await ui.elements.get("language-ar").click(); await ui.settle();
  home = ui.elements.get("report-content");
  assert.match(home.textContent, /لا أخبار محفوظة بعد.*اضغط تحديث الأخبار لجلب/);
  assert.doesNotMatch(home.textContent, /اكتمل فحص المصدر/);
  assert.equal(ui.newsRequests().every(request => request.options.method === "GET"), true);
});

test("manual refresh polls the saved result until complete and stops making idle requests", async () => {
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", newsFeed()]],
    fetch: (url, request, respond) => {
      if (url.endsWith("/news/refresh")) return respond(newsFeed({status: "loading", refresh_started: true}));
    }});
  await byClass(ui.elements.get("report-content"), "news-refresh")[0].click(); await ui.settle();
  assert.match(ui.elements.get("report-content").textContent, /Refreshing news/);
  assert.equal(ui.newsRequests().length, 2);
  assert.equal(ui.newsRequests()[1].options.method, "POST");
  assert.deepEqual(JSON.parse(ui.newsRequests()[1].options.body), {language: "en"});
  ui.server.feeds.set(A + ":en", newsFeed({items: [newsItem(2, {headline: "Manually refreshed article"})]}));
  await ui.advance(2000);
  assert.match(ui.elements.get("report-content").textContent, /Manually refreshed article/);
  assert.deepEqual(ui.newsRequests().map(request => request.options.method), ["GET", "POST", "GET"]);
  ui.setNow(NEWS_NOW + 60 * 60000); await ui.advance(60 * 60000);
  assert.equal(ui.newsRequests().length, 3, "Completion must stop status polling and must not schedule a later fetch.");
  assert.equal(ui.generations().length, 0); assert.equal(ui.messages().length, 0);
});

test("provider-error payload keeps the last successful refresh and stale articles expire without becoming fresh", async () => {
  const data = newsFeed({status: "provider_error", stale: true, partial: true,
    items: [newsItem(), newsItem(10, {published_at: "2026-10-19T23:00:00Z"})],
    error: {code: "newsdata_rate_limited", retryable: true, retry_after_seconds: 300, reference: "news_rate_1"}});
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", data]]});
  const home = ui.elements.get("report-content");
  assert.match(home.textContent, /News refresh failed.*request limit/);
  assert.match(home.textContent, /Retry after 300 seconds/); assert.match(home.textContent, /last successful refresh is stale/);
  assert.match(home.textContent, /Coverage is partial/); assert.match(home.textContent, /1 articles from the last 12 hours/);
  assert.doesNotMatch(home.textContent, /Live article 10/);
  assert.equal(byClass(home, "news-feed-status")[0].classList.contains("fresh"), false);
});

test("loading feed polls saved status and a successful empty response is labelled separately", async () => {
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", newsFeed({status: "loading", items: [], last_successful_refresh_at: null})]]});
  assert.match(ui.elements.get("report-content").textContent, /Refreshing news.*No successful refresh yet/);
  ui.server.feeds.set(A + ":en", newsFeed({status: "empty", items: [], pagination: {raw_count: 12}}));
  await ui.advance(2000);
  const home = ui.elements.get("report-content");
  assert.match(home.textContent, /No qualifying articles/); assert.match(home.textContent, /The source check completed without articles/);
  assert.match(home.textContent, /12 provider records fetched/);
  assert.equal(ui.newsRequests().length, 2); assert.equal(ui.newsRequests().some(r => r.options.method === "POST"), false);
  assert.equal(ui.generations().length, 0);
});

test("idle news views never request automatic updates; reactivation may only read the saved cache", async () => {
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", newsFeed()]]});
  await ui.advance(10000); assert.equal(ui.newsRequests().length, 1);
  ui.setNow(NEWS_NOW + 15 * 60000); await ui.advance(900000);
  assert.equal(ui.newsRequests().length, 1, "Leaving the news view open past the refresh interval must not make a request.");
  await ui.visibility(true); await ui.advance(900000); assert.equal(ui.newsRequests().length, 1);
  ui.server.feeds.set(A + ":en", newsFeed({next_refresh_at: "2026-10-20T12:30:00Z"}));
  ui.setNow(NEWS_NOW + 15 * 60000); await ui.visibility(false);
  assert.equal(ui.newsRequests().length, 2, "A due visible snapshot must check the cache immediately.");
  await ui.advance(60000); assert.equal(ui.newsRequests().length, 2);
  await ui.elements.get("nav-home").click(); await ui.settle(); await ui.advance(900000);
  assert.equal(ui.newsRequests().length, 2); assert.equal(ui.generations().length, 0);
  assert.equal(ui.newsRequests().every(request => request.options.method === "GET"), true);
});

test("an idle view ages its saved snapshot and removes expired articles without fetching news", async () => {
  const item = newsItem(1, {published_at: new Date(NEWS_NOW - 12 * 60 * 60000 + 60000).toISOString()});
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", newsFeed({items: [item]})]]});
  assert.match(ui.elements.get("report-content").textContent, /Live article 1/);
  ui.setNow(NEWS_NOW + 60001); await ui.advance(60001);
  let home = ui.elements.get("report-content");
  assert.doesNotMatch(home.textContent, /Live article 1/);
  assert.match(home.textContent, /0 articles from the last 12 hours.*saved cache has no articles that qualify/);
  ui.setNow(NEWS_NOW + 15 * 60000); await ui.advance(15 * 60000);
  home = ui.elements.get("report-content");
  assert.match(home.textContent, /Cached.*last successful refresh is stale/);
  assert.equal(ui.newsRequests().length, 1, "Display ageing must not request a new feed or report.");
  assert.equal(ui.generations().length, 0); assert.equal(ui.messages().length, 0);
});

test("Arabic and English feeds keep their own data and identify missing translation", async () => {
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", newsFeed()], [A + ":ar", newsFeed({items: [
    newsItem(1, {headline_ar: "خبر عربي موثّق", summary_ar: "ملخص الخبر بالعربية", display_language: "ar"}), newsItem(2)
  ]})]]});
  await ui.elements.get("language-ar").click(); await ui.settle();
  const home = ui.elements.get("report-content");
  assert.equal(home.dir, "rtl"); assert.match(home.textContent, /خبر عربي موثّق/); assert.match(home.textContent, /آخر تحديث ناجح/);
  assert.match(home.textContent, /الترجمة إلى لغة الواجهة غير متاحة/);
  assert.match(home.textContent, /تحديث يدوي/);
  assert.doesNotMatch(home.textContent, /تحديث تلقائي/);
  assert.equal(ui.newsRequests().length, 2); assert.match(ui.newsRequests()[1].url, /language=ar$/);
  await ui.elements.get("language-en").click(); await ui.settle();
  assert.match(home.textContent, /Live article 1/); assert.doesNotMatch(home.textContent, /خبر عربي موثّق/);
  assert.equal(ui.newsRequests().every(request => request.options.method === "GET"), true, "Language switches only read saved feeds.");
  assert.equal(ui.generations().length, 0); assert.equal(ui.messages().length, 0);
});

test("switching back to an expired language snapshot immediately reads the shared cache without duplicate requests", async () => {
  let englishChecks = 0, release;
  const refreshed = newsFeed({last_successful_refresh_at: "2026-10-20T12:16:00Z", next_refresh_at: "2026-10-20T12:31:00Z",
    items: [newsItem(1, {headline: "Updated cached English article"})]});
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", newsFeed()], [A + ":ar", refreshed]],
    fetch: (url, request, respond) => {
      if (url.endsWith("/news?language=en") && ++englishChecks === 2)
        return new Promise(resolve => { release = () => resolve(respond(refreshed)); });
    }});
  ui.setNow(NEWS_NOW + 22 * 60000);
  await ui.elements.get("language-ar").click(); await ui.settle();
  assert.equal(ui.newsRequests().length, 2);
  await ui.elements.get("language-en").click(); await ui.settle();
  assert.equal(ui.newsRequests().length, 3, "The expired English snapshot must not wait for the 60-second timer floor.");
  assert.match(ui.newsRequests()[2].url, /\/news\?language=en$/);
  await ui.elements.get("language-en").click(); await ui.visibility(false);
  assert.equal(ui.newsRequests().length, 3, "Repeated activation must join the in-flight cache check.");
  release(); await ui.settle();
  assert.match(ui.elements.get("report-content").textContent, /Updated cached English article/);
  assert.equal(ui.newsState(A, "en").data.last_successful_refresh_at, refreshed.last_successful_refresh_at);
  await ui.elements.get("language-ar").click(); await ui.elements.get("language-en").click(); await ui.settle();
  await ui.advance(60000);
  assert.equal(ui.newsRequests().length, 3, "A refreshed snapshot must not trigger another activation request or loop.");
  assert.equal(ui.newsRequests().every(request => request.options.method === "GET"), true);
  assert.equal(ui.generations().length, 0); assert.equal(ui.messages().length, 0);
});

test("activating a loading or paused feed preserves its existing polling gates", async () => {
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", newsFeed({status: "loading", next_refresh_at: "2026-10-20T11:59:00Z"})]]});
  await ui.visibility(true); await ui.visibility(false);
  assert.equal(ui.newsRequests().length, 1, "Loading status must retain its polling interval rather than immediately repeat.");
  await ui.advance(2000); assert.equal(ui.newsRequests().length, 2);
  ui.newsState(A).pollPaused = true;
  await ui.visibility(true); await ui.visibility(false); await ui.advance(900000);
  assert.equal(ui.newsRequests().length, 2, "Activation must not restart a paused polling loop.");
});

test("expired snapshot activation respects provider cooldowns and nonretryable response or transport errors", async () => {
  for (const mode of ["provider", "transport", "cooldown"]) {
    const error = {code: mode === "cooldown" ? "newsdata_rate_limited" : "newsdata_auth_failed",
      retryable: mode === "cooldown", ...(mode === "cooldown" ? {retry_after_seconds: 300} : {})};
    let checks = 0;
    const ui = await startNews({now: NEWS_NOW, newsAgents: [A], fetch: (url, request, respond) => {
      if (!url.includes("/news?")) return;
      checks++;
      if (mode === "transport" && checks > 1) return respond({detail: error}, 502);
      return respond(newsFeed({next_refresh_at: "2026-10-20T11:59:00Z",
        ...(mode !== "transport" ? {status: "provider_error", error} : {})}));
    }});
    if (mode === "transport") await ui.loadNews(A);
    const count = ui.newsRequests().length;
    await ui.visibility(true); await ui.visibility(false); await ui.advance(60000);
    assert.equal(ui.newsRequests().length, count, mode + " must retain its retry gate when activated.");
    assert.equal(ui.generations().length, 0); assert.equal(ui.messages().length, 0);
  }
});

test("the observed twelve-hour provider delay is an honest empty result without inferring the account plan", async () => {
  const delayed = newsFeed({status: "empty", items: [], pagination: {raw_count: 10}, limitation_codes: ["provider_delay", "provider_plan_unverified"],
    suspected_provider_delay: true, observed_latest_provider_publication_at: "2026-10-19T23:59:50Z", provider_age_seconds: 43210,
    provider_plan: {configured_plan: "unknown", plan_verified: false}});
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", delayed], [A + ":ar", delayed]]});
  let home = ui.elements.get("report-content");
  assert.match(home.textContent, /No qualifying articles.*latest available articles are outside the last 12-hour window/);
  assert.match(home.textContent, /may have a 12-hour delay.*Latest publication returned by the source.*03:59/);
  assert.match(home.textContent, /10 provider records fetched/);
  assert.doesNotMatch(home.textContent, /confirmed free|News refresh failed|Retry report/);
  await ui.elements.get("language-ar").click(); await ui.settle(); home = ui.elements.get("report-content");
  assert.match(home.textContent, /لا أخبار مستوفية للشروط.*أحدث المقالات المتاحة من المصدر تقع خارج/);
  assert.match(home.textContent, /قد يقدم الوصول الحالي أخبارًا متأخرة/);
  assert.doesNotMatch(home.textContent, /المجانية المؤكدة|تعذر تحديث الأخبار/);
  assert.equal(ui.generations().length, 0); assert.equal(ui.messages().length, 0);
});

test("a provider cooldown disables repeated manual refresh and unlocks without an automatic report request", async () => {
  const limited = newsFeed({status: "provider_error", error: {code: "newsdata_rate_limited", retry_after_seconds: 30, retryable: true}});
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", limited]]});
  let refresh = byClass(ui.elements.get("report-content"), "news-refresh")[0];
  assert.equal(refresh.disabled, true); await refresh.click(); await ui.loadNews(A, true);
  assert.equal(ui.newsRequests().length, 1);
  ui.setNow(NEWS_NOW + 30000); await ui.advance(30000);
  refresh = byClass(ui.elements.get("report-content"), "news-refresh")[0]; assert.equal(refresh.disabled, false);
  assert.doesNotMatch(ui.elements.get("report-content").textContent, /Retry after 30 seconds/);
  ui.server.feeds.set(A + ":en", newsFeed()); await refresh.click(); await ui.settle();
  assert.equal(ui.newsRequests().filter(r => r.options.method === "POST").length, 1);
  assert.equal(ui.generations().length, 0);
});

test("a failed loading-status poll stops the spinner and keeps previous articles with a precise retry message", async () => {
  let checks = 0;
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], fetch: (url, request, respond) => {
    if (url.includes("/news?")) return ++checks === 1 ? respond(newsFeed({status: "loading"})) :
      respond({detail: {code: "newsdata_connection_error", retryable: true, reference: "news_connection_1"}}, 502);
  }});
  await ui.advance(2000);
  const home = ui.elements.get("report-content");
  assert.match(home.textContent, /News refresh failed.*could not be reached/); assert.match(home.textContent, /Live article 1/);
  assert.doesNotMatch(home.textContent, /Refreshing news|The source check completed without/);
  assert.equal(byClass(home, "news-refresh")[0].disabled, false);
  await ui.advance(2000); assert.equal(checks, 2, "A failed status check must not continue fast polling.");
});

test("agent total follows configuration without hardcoded removed cards", async () => {
  const seven = Array.from({length: 7}, (_, i) => ({...profiles[i % 2], id: "active-agent-" + i}));
  const ui = await start({profiles: seven});
  assert.equal(byClass(ui.elements.get("agent-grid"), "agent-card").length, 7);
  assert.match(ui.elements.get("agent-count").textContent, /7 specialist agents/);
});

test("the public workspace has no credential, cost, admin, or upload controls", async () => {
  assert.doesNotMatch(html, /type="(?:password|file)"|id="(?:username|password)"|\/admin|dailyCost|monthlyCost|accountOverview|usage-details|loginBox/);
  assert.match(html, /workspace\.js\?v=2\.1\.0"/);
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

test("home has only agent cards and selecting or switching an agent performs saved reads only", async () => {
  const ui = await start({now: NEWS_NOW, newsAgents: [A, B]});
  assert.equal(ui.newsRequests().length, 0); assert.equal(ui.requests.length, 2);
  assert.doesNotMatch(html, /id="(?:home-news|nav-chat|main-chat-view)"/);
  const cards = byClass(ui.elements.get("agent-grid"), "agent-card");
  assert.equal(cards.length, 2); await cards[0].click(); await ui.settle();
  assert.equal(ui.selected(), A); assert.equal(ui.elements.get("workspace-view").hidden, false);
  assert.equal(ui.elements.get("research-tools").open, false);
  assert.equal(ui.chatState(A).loaded, true);
  await ui.openAgent(B, true); await ui.settle();
  await ui.elements.get("language-ar").click(); await ui.settle(); await ui.advance(900000);
  assert.equal(ui.selected(), B); assert.equal(ui.chatState(B).loaded, true);
  assert.equal(ui.requests.every(request => request.options.method === "GET"), true);
  assert.equal(ui.generations().length, 0); assert.equal(ui.messages().length, 0); assert.equal(ui.newsRequests().length, 0);
  await ui.generateReport(B); await ui.settle();
  assert.equal(ui.generations().length, 1); assert.equal(ui.elements.get("research-tools").open, true);
  assert.match(ui.generations()[0].url, new RegExp(B + "/reports$"));
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
  await ui.openAgent(A); await ui.settle();
  const first = ui.generateReport(A); await ui.settle();
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
  await ui.openAgent(A); await ui.generateReport(A); await ui.settle(); await topicKey(ui);
  assert.equal(ui.generations().length, 2);
  assert.equal(ui.generations()[0].options.headers["Idempotency-Key"], ui.generations()[1].options.headers["Idempotency-Key"]);
  assert.doesNotMatch(ui.body.textContent, /PRIVATE|provider key|cost diagnostic/);
});

for (const language of ["en", "ar"]) {
  test(`an asynchronous daily report limit explains the Dubai reset in ${language} and keeps the saved report`, async () => {
    const ui = await start({language}), saved = seed(ui);
    await ui.openAgent(A); await ui.generateReport(A); await ui.settle();
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
  const saved = seed(ui); await ui.openAgent(A); await ui.generateReport(A); await ui.settle();
  assert.match(ui.elements.get("report-notice").textContent, /Today's report limit.*00:00 Dubai time/);
  assert.doesNotMatch(ui.body.textContent, /PRIVATE/);
  assert.equal(ui.current().report.report_id, saved.report_id);
});

for (const [code, english, arabic] of [
  ["daily_budget_limit", /daily allowance.*next midnight/, /الاستخدام اليومي.*منتصف الليل التالي/],
  ["monthly_budget_limit", /monthly allowance.*next month/, /الاستخدام الشهري.*الشهر التالي/]
]) {
  test(`asynchronous ${code} describes the allowance without exposing costs in either language`, async () => {
    const ui = await start(); seed(ui); await ui.openAgent(A); await ui.generateReport(A); await ui.settle();
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

for (const language of ["en", "ar"]) {
  test(`reload restores the precise terminal report failure and previous snapshot in ${language}`, async () => {
    const session = new Map([["aihub.workspace.agent", A]]), saved = report(A, 1);
    const failed = job(A, 2, {status: "failed", stage: "checking_sources", error_code: "report_generation_failed",
      error: {code: "invalid_report_stage", reference: "job_progress_failure_1", stage: "checking_sources", retryable: false, message: "PRIVATE provider prose"}});
    const ui = await start({language, session, fetch: (url, request, respond) => {
      if (url === PREFIX + "/agents/" + A) return respond({last_successful_report: saved, active_job: null, last_job: failed});
    }});
    const notice = ui.elements.get("report-notice");
    assert.match(notice.textContent, language === "en" ? /service stopped while updating report progress/ : /توقفت الخدمة أثناء تحديث حالة التقرير/);
    assert.match(notice.textContent, language === "en" ? /Attempt stopped at: Checking sources/ : /توقفت المحاولة عند: التحقق من المصادر/);
    assert.match(notice.textContent, /job_progress_failure_1/); assert.doesNotMatch(ui.body.textContent, /PRIVATE/);
    assert.equal(ui.current().report.report_id, saved.report_id); assert.equal(ui.elements.get("pdf-download").hidden, false);
    const previous = byTag(notice, "button").find(b => b.textContent === (language === "en" ? "Open previous report" : "فتح التقرير السابق"));
    assert.ok(previous); await previous.click(); await ui.settle();
    assert.match(ui.elements.get("report-meta").textContent, language === "en" ? /Previous successful report/ : /التقرير الناجح السابق/);
    assert.equal(ui.current().tab, "pack"); assert.match(ui.elements.get("report-content").textContent, /Executive summary/);
    assert.equal(ui.generations().length, 0); assert.equal(ui.elements.get("job-progress").hidden, true);
  });
}

test("actual saving progress remains visible until completion and the saved result becomes ready", async () => {
  const ui = await start(); seed(ui); await ui.openAgent(A); await ui.generateReport(A); await ui.settle();
  const running = ui.current().job;
  ui.server.jobs.set(running.job_id, {job: {...running, status: "running", stage: "saving"}, report: null});
  await ui.advance();
  assert.equal(byClass(ui.elements.get("job-progress"), "job-title")[0].textContent, "Saving report");
  assert.match(ui.elements.get("job-progress").textContent, /Status last checked/);
  await ui.elements.get("language-ar").click(); assert.match(ui.elements.get("job-progress").textContent, /حفظ التقرير/);
  ui.server.jobs.set(running.job_id, {job: {...running, status: "ready", stage: "ready"}, report: report(A, running.generation_seq)});
  await ui.advance();
  assert.match(ui.elements.get("job-progress").textContent, /التقرير جاهز/);
  assert.equal(ui.current().report.generation_seq, running.generation_seq); assert.equal(ui.generations().length, 1);
});

test("saved report counts and provider limitations remain separate from the current live feed", async () => {
  const ui = await start({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", newsFeed()]]}), saved = seed(ui);
  Object.assign(saved.result, {actual_article_count: 0, analyzed_article_count: 0, requested_article_target: 100,
    news_feed: {last_successful_refresh_at: "2026-10-20T11:00:00Z", limitation_codes: ["provider_delay"],
      observed_latest_provider_publication_at: "2026-10-19T22:59:50Z"}, freshness: {suspected_provider_delay: true}});
  await ui.openAgent(A, false); await ui.settle();
  await openResearch(ui);
  assert.equal(ui.elements.get("tab-evidence").textContent, "Live news");
  assert.match(ui.elements.get("report-content").textContent, /Live article 1/);
  assert.doesNotMatch(ui.elements.get("report-content").textContent, /current access may have a 12-hour delay/);
  await ui.elements.get("tab-pack").click();
  const meta = ui.elements.get("report-meta");
  assert.match(meta.textContent, /0 qualifying news items.*Target: up to 100 articles.*0 articles analyzed/);
  assert.match(meta.textContent, /News snapshot used for this report.*latest available articles are outside/);
  assert.equal(ui.generations().length, 0);
});

test("a retryable terminal failure keeps its safe reference and retries only after explicit action and the cooldown", async () => {
  const ui = await start({now: NEWS_NOW}); seed(ui); await ui.openAgent(A); await ui.generateReport(A); await ui.settle();
  const failed = ui.current().job, firstKey = ui.generations()[0].options.headers["Idempotency-Key"];
  ui.server.jobs.set(failed.job_id, {job: {...failed, status: "failed", error_code: "report_generation_failed", error: {
    code: "openai_timeout", reference: "job_timeout_1", stage: "searching", retryable: true, retry_after_seconds: 30, message: "PRIVATE $99 diagnostic"}}, report: null});
  await ui.advance();
  let notice = ui.elements.get("report-notice"), retry = byTag(notice, "button").find(b => b.textContent === "Retry report");
  assert.match(notice.textContent, /timed out.*Retry after 30 seconds.*job_timeout_1/);
  assert.doesNotMatch(ui.body.textContent, /PRIVATE|\$99/); assert.equal(retry.disabled, true);
  await retry.click(); await topicKey(ui); assert.equal(ui.generations().length, 1);
  ui.setNow(NEWS_NOW + 30000); await ui.advance(30000);
  notice = ui.elements.get("report-notice"); retry = byTag(notice, "button").find(b => b.textContent === "Retry report");
  assert.equal(retry.disabled, false); assert.equal(ui.generations().length, 1);
  await retry.click(); await ui.settle();
  assert.equal(ui.generations().length, 2); assert.notEqual(ui.generations()[1].options.headers["Idempotency-Key"], firstKey);
  assert.equal(ui.current().job.generation_seq, failed.generation_seq + 1);
});

test("first-attempt failure does not claim that a previous report exists and precise provider failures stay bilingual", async () => {
  for (const [code, english, arabic] of [
    ["unclassified_failure", /could not be completed/, /تعذر إكمال التقرير/],
    ["openai_auth_failed", /provider rejected this request/, /لم يقبل مزود البحث هذا الطلب/],
    ["openai_access_denied", /provider rejected this request/, /لم يقبل مزود البحث هذا الطلب/],
    ["openai_request_rejected", /provider rejected this request/, /لم يقبل مزود البحث هذا الطلب/],
    ["openai_quota_exceeded", /service allowance is exhausted/, /استُنفدت حصة الخدمة/],
    ["openai_rate_limited", /rate-limiting requests/, /يقيّد الطلبات/],
    ["openai_unavailable", /temporarily unavailable/, /غير متاح مؤقتًا/],
    ["search_call_limit_exceeded", /exceeded the permitted number of search actions.*operation was stopped/, /تجاوز مزوّد البحث العدد المسموح.*أُوقفت العملية/]
  ]) {
    const ui = await start(); await ui.openAgent(A); await ui.generateReport(A); await ui.settle(); const failed = ui.current().job;
    ui.server.jobs.set(failed.job_id, {job: {...failed, status: "failed", error: {code, reference: "job_safe_1", retryable: false}}, report: null});
    await ui.advance(); assert.match(ui.elements.get("report-notice").textContent, english);
    assert.equal(byTag(ui.elements.get("report-notice"), "button").length, 0);
    assert.doesNotMatch(ui.elements.get("report-notice").textContent, /The previous report remains|Open previous report/);
    await ui.elements.get("language-ar").click(); assert.match(ui.elements.get("report-notice").textContent, arabic);
    assert.equal(ui.generations().length, 1);
  }
});

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
  await ui.openAgent(A); await ui.generateReport(A); await ui.settle();
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
  assert.equal(body.agent_id, A); assert.equal(body.report_id, saved.report_id); assert.equal(Object.hasOwn(body, "topic"), false);
  assert.equal(body.message, "Why does this finding matter?"); assert.equal(message.options.headers.Authorization, undefined);
  assert.match(ui.elements.get("discussion-panel").textContent, /Saved evidence answer/);
  assert.equal(byTag(ui.elements.get("discussion-panel"), "input").length, 0);
  assert.equal(ui.generations().length, 0);
});

test("chat questions do not inherit a saved report topic or the report generator's edited topic", async () => {
  const ui = await start();
  const saved = report(A, 1, {topic: "Canada education policy"});
  ui.server.reports.set(saved.report_id, saved);
  await ui.openSaved(saved.report_id, A); await ui.settle();
  assert.equal(ui.elements.get("topic-input").value, saved.topic);
  await typeChat(ui, "What changed in Finland education policy?");
  await ui.sendChat(ui.chatState(A)); await ui.settle();
  const topic = ui.elements.get("topic-input"); topic.value = "Japan education policy"; await topic.emit("input");
  await typeChat(ui, "Which Finland sources support that answer?");
  await ui.sendChat(ui.chatState(A)); await ui.settle();
  assert.equal(ui.messages().length, 2);
  for (const message of ui.messages()) {
    const body = JSON.parse(message.options.body);
    assert.equal(body.report_id, saved.report_id);
    assert.equal(Object.hasOwn(body, "topic"), false, "Report context must not become an explicit new chat research topic.");
    assert.match(body.message, /Finland/);
  }
  assert.equal(ui.generations().length, 0);
  await ui.generateReport(A); await ui.settle();
  assert.equal(JSON.parse(ui.generations()[0].options.body).topic, "Japan education policy");
});

test("chat evidence counts read naturally for one or multiple saved reports in both languages", async () => {
  for (const count of [1, 2]) {
    const reportIds = Array.from({length: count}, (_, index) => report(A, index + 1).report_id);
    const ui = await start({fetch: (url, request, respond) => {
      if (request.method === "POST" && url.endsWith("/messages/stream"))
        return respond(answer(JSON.parse(request.body), {context: {report_ids: reportIds, agent_ids: [A]}}));
    }});
    await ui.openAgent(A); await ui.settle(); await typeChat(ui, "Summarize the saved reports.");
    await ui.sendChat(ui.chatState(A)); await ui.settle();
    assert.equal(byClass(ui.elements.get("discussion-panel"), "chat-evidence-note")[0].textContent, "Saved report snapshots used: " + count + ".");
    await ui.elements.get("language-ar").click(); await ui.settle();
    assert.equal(byClass(ui.elements.get("discussion-panel"), "chat-evidence-note")[0].textContent, "عدد نسخ التقارير المحفوظة المستخدمة: " + count + ".");
    assert.equal(ui.messages().length, 1); assert.equal(ui.generations().length, 0);
  }
});

test("fresh chat waits for the saved report then answers with the same request key", async () => {
  const newer = report(A, 2), freshJob = job(A, 2);
  let messageRequests = 0;
  const ui = await start({fetch: (url, request, respond) => {
    if (request.method === "POST" && url.endsWith("/messages/stream")) {
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
    if (request.method === "POST" && url.endsWith("/messages/stream"))
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

test("each agent restores its durable thread, isolated history and draft after switching and reloading", async () => {
  const ui = await start(); await ui.openAgent(A); await ui.settle();
  await typeChat(ui, "Unsent agent A draft"); const firstId = ui.chatState(A).id;
  await ui.openAgent(B); await ui.settle();
  assert.notEqual(ui.chatState(B).id, firstId);
  assert.equal(ui.elements.get("discussion-panel").querySelector("textarea").value, "");
  await typeChat(ui, "Education question"); await ui.sendChat(ui.chatState(B)); await ui.settle();
  assert.match(ui.elements.get("discussion-panel").textContent, /Education question/);
  await ui.openAgent(A); await ui.settle();
  assert.equal(ui.elements.get("discussion-panel").querySelector("textarea").value, "Unsent agent A draft");
  assert.doesNotMatch(ui.elements.get("discussion-panel").textContent, /Education question/);
  assert.equal(byTag(ui.elements.get("discussion-panel"), "select").length, 0);
  assert.doesNotMatch(ui.elements.get("discussion-panel").textContent, /New chat|Saved conversations/);
  const reload = await start({server: ui.server, session: new Map([["aihub.workspace.agent", B]])});
  assert.equal(reload.chatState(B).id, ui.chatState(B).id);
  assert.match(reload.elements.get("discussion-panel").textContent, /Education question.*Saved evidence answer/);
  assert.equal(reload.generations().length, 0); assert.equal(reload.messages().length, 0);
  assert.equal(reload.requests.every(request => request.options.method === "GET"), true);
});

test("stale browser chat IDs cannot choose or corrupt a restored agent conversation", async () => {
  const session = new Map([["aihub.workspace.agent", A], ["aihub.workspace.chat." + A, "محادثات محفوظة"]]);
  const ui = await start({session, language: "ar"});
  assert.equal(ui.chatState(A).id, conversationId(1));
  assert.equal(ui.requests.some(request => request.url.includes("محادثات") || request.url.includes("/public/chat/")), false);
  assert.equal(ui.chatState(A).error, null); assert.equal(ui.chatState(A).loaded, true);
  assert.equal(ui.generations().length, 0); assert.equal(ui.messages().length, 0);
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
    if (request.method === "POST" && path.endsWith("/messages/stream"))
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

function eventStream() {
  const queue = []; let pending = null, ended = false;
  const encoder = new TextEncoder();
  const deliver = value => {if(pending){const resolve=pending;pending=null;resolve(value);}else queue.push(value);};
  return {
    response: {ok: true, status: 200, headers: {get: name => name.toLowerCase() === "content-type" ? "text/event-stream; charset=utf-8" : null},
      body: {getReader: () => ({read: () => queue.length ? Promise.resolve(queue.shift()) : ended ? Promise.resolve({done:true}) : new Promise(resolve => {pending=resolve;}), releaseLock(){}})}},
    bytes(bytes){deliver({done:false,value:bytes});},
    event(name,data){deliver({done:false,value:encoder.encode("event: "+name+"\ndata: "+JSON.stringify(data)+"\n\n")});},
    close(){ended=true;deliver({done:true});}
  };
}

test("actual stream deltas are visible before completion and become safe Markdown only when complete", async () => {
  const stream=eventStream();
  const ui=await start({fetch:(url)=>url.endsWith("/messages/stream")?stream.response:undefined});
  await ui.openAgent(A);await ui.settle();await typeChat(ui,"Explain the evidence.");
  const sending=ui.sendChat(ui.chatState(A));await ui.settle();
  assert.equal(ui.messages().length,1);assert.equal(ui.messages()[0].options.headers.Accept,"text/event-stream");
  stream.event("status",{status:"answering"});
  const content="**Evidence** [Source](https://publisher.example/synthetic).\n\nأهلًا بالعالم";
  const bytes=new TextEncoder().encode("event: delta\r\ndata: "+JSON.stringify({delta:content})+"\r\n\r\n");
  const arabicStart=bytes.findIndex(byte=>byte>127);
  stream.bytes(bytes.slice(0,arabicStart+1));stream.bytes(bytes.slice(arabicStart+1,bytes.length-1));stream.bytes(bytes.slice(-1));
  await ui.settle();
  const panel=ui.elements.get("discussion-panel");
  assert.equal(ui.chatState(A).sending,true);assert.match(panel.textContent,/أهلًا بالعالم/);
  assert.equal(byClass(panel,"streaming-text")[0].textContent,content);
  assert.equal(byTag(panel,"a").length,0);assert.equal(byTag(panel,"strong").length,0);
  assert.equal(byClass(panel,"pending-message").length,1);
  stream.event("complete",answer({message:"Explain the evidence.",agent_id:A},{message:{id:"stream_answer",role:"assistant",content,created_at:"2026-10-20T10:00:00Z"}}));
  await sending;await ui.settle();
  assert.equal(ui.chatState(A).sending,false);assert.equal(byClass(panel,"streaming-text").length,0);
  assert.equal(byTag(panel,"strong")[0].textContent,"Evidence");
  assert.equal(byTag(panel,"a")[0].href,"https://publisher.example/synthetic");
  assert.equal(byTag(panel,"time")[0].getAttribute("datetime"),"2026-10-20T10:00:00Z");
  assert.match(byTag(panel,"time")[0].textContent,/14:00/);
  assert.equal(ui.generations().length,0);
});

test("switching agents during a streamed answer preserves each thread and never starts another turn", async () => {
  const stream=eventStream();
  const ui=await start({fetch:url=>url.endsWith("/messages/stream")?stream.response:undefined});
  await ui.openAgent(A);await ui.settle();await typeChat(ui,"Agent A question");
  const sending=ui.sendChat(ui.chatState(A));await ui.settle();stream.event("delta",{delta:"Agent A partial"});await ui.settle();
  await ui.openAgent(B);await ui.settle();await typeChat(ui,"Agent B draft");
  stream.event("delta",{delta:" and final"});stream.event("complete",answer({message:"Agent A question",agent_id:A},{message:{id:"agent_a_final",role:"assistant",content:"Agent A final answer"}}));
  await sending;await ui.settle();
  assert.equal(ui.selected(),B);assert.doesNotMatch(ui.elements.get("discussion-panel").textContent,/Agent A (?:partial|final|question)/);
  assert.equal(ui.elements.get("discussion-panel").querySelector("textarea").value,"Agent B draft");
  assert.equal(ui.messages().length,1);assert.equal(ui.generations().length,0);
  await ui.openAgent(A);await ui.settle();assert.match(ui.elements.get("discussion-panel").textContent,/Agent A final answer/);
  assert.equal(ui.chatState(A).messages.length,2);assert.equal(ui.chatState(B).messages.length,0);
});

test("interrupted streaming preserves the request key for explicit replay without duplicating messages", async () => {
  const first=eventStream(),second=eventStream();let attempts=0;
  const ui=await start({fetch:url=>url.endsWith("/messages/stream")?(++attempts===1?first:second).response:undefined});
  await ui.openAgent(A);await ui.settle();await typeChat(ui,"One durable turn");
  const sending=ui.sendChat(ui.chatState(A));await ui.settle();first.event("delta",{delta:"Partial [citation](https://publisher.example/synthetic)"});first.close();
  await sending;await ui.settle();
  assert.equal(ui.chatState(A).sending,false);assert.equal(ui.chatState(A).messages.length,0);
  assert.equal(byTag(ui.elements.get("discussion-panel"),"a").length,0);
  assert.match(ui.elements.get("discussion-panel").textContent,/Incomplete response/);
  await ui.advance(900000);assert.equal(ui.messages().length,1,"An interruption never retries a provider request automatically.");
  const retry=ui.sendChat(ui.chatState(A),true);await ui.settle();
  assert.equal(ui.messages()[0].options.headers["Idempotency-Key"],ui.messages()[1].options.headers["Idempotency-Key"]);
  second.event("complete",answer({message:"One durable turn",agent_id:A}));await retry;await ui.settle();
  assert.equal(ui.chatState(A).messages.length,2);assert.equal(ui.chatState(A).attempt,null);
  assert.equal(byClass(ui.elements.get("discussion-panel"),"streaming-text").length,0);
});

test("stream errors distinguish provider quota from app allowance and keep unvalidated partial text unlinked", async () => {
  for(const [code,expected]of [["openai_quota_exceeded",/service allowance is exhausted/],["daily_budget_limit",/daily allowance.*next midnight/]]){
    const stream=eventStream();const ui=await start({fetch:url=>url.endsWith("/messages/stream")?stream.response:undefined});
    await ui.openAgent(A);await ui.settle();await typeChat(ui,"Question");const sending=ui.sendChat(ui.chatState(A));await ui.settle();
    stream.event("delta",{delta:"Unfinished [Source](https://publisher.example/synthetic)"});
    stream.event("error",{code,message:"PRIVATE provider account diagnosis",turn_id:"turn_safe_reference",retryable:false});
    await sending;await ui.settle();const panel=ui.elements.get("discussion-panel");
    assert.match(panel.textContent,expected);assert.match(panel.textContent,/turn_safe_reference/);
    assert.doesNotMatch(panel.textContent,/PRIVATE/);assert.equal(byTag(panel,"a").length,0);
    assert.equal(ui.chatState(A).messages.length,0);assert.equal(ui.chatState(A).attempt,null);
  }
});

test("earlier messages load only within the immutable agent thread and reject a mismatched restore", async () => {
  const id=conversationId(9);const ui=await start({fetch:(url,request,respond)=>{
    if(url.includes("/agents/"+A+"/conversation"))return respond({conversation_id:id,agent_id:A,messages:[{id:"newer",role:"assistant",content:"Newer saved answer"}],pagination:{has_more:true,next_before:30}});
    if(url.includes("/conversations/"+id+"?before=30"))return respond({conversation_id:id,agent_id:A,messages:[{id:"earlier",role:"user",content:"Earlier saved question"}],pagination:{has_more:false}});
    if(url.includes("/agents/"+B+"/conversation"))return respond({conversation_id:id,agent_id:A,messages:[{id:"wrong",role:"assistant",content:"Must remain private to A"}],pagination:{has_more:false}});
  }});
  await ui.openAgent(A);await ui.settle();const earlier=byTag(ui.elements.get("discussion-panel"),"button").find(button=>button.textContent==="Earlier messages");
  assert.ok(earlier);await earlier.click();await ui.settle();
  assert.deepEqual(clone(ui.chatState(A).messages.map(message=>message.id)),["earlier","newer"]);
  await ui.openAgent(B);await ui.settle();
  assert.equal(ui.chatState(B).loaded,false);assert.equal(ui.chatState(B).id,null);
  assert.doesNotMatch(ui.elements.get("discussion-panel").textContent,/Must remain private|Newer saved answer/);
  assert.equal(byClass(ui.elements.get("discussion-panel"),"chat-send")[0].disabled,true);
  assert.equal(ui.requests.every(request=>request.options.method==="GET"),true);
});

test("shared user notes require explicit approval and remain separate from published evidence", async () => {
  const ui=await start();await ui.openAgent(A);await ui.settle();
  const panel=ui.elements.get("shared-memory");panel.open=true;await panel.emit("toggle");await ui.settle();
  assert.match(panel.textContent,/separate from published evidence/);
  ui.elements.get("memory-kind").value="preference";ui.elements.get("memory-input").value="Prefer concise Arabic summaries.";
  await ui.elements.get("memory-form").requestSubmit();await ui.settle();
  assert.equal(ui.server.memory[0].status,"proposed");assert.match(panel.textContent,/Awaiting your review/);
  assert.equal(ui.requests.filter(r=>r.options.method==="PATCH").length,0);
  await byTag(ui.elements.get("memory-items"),"button").find(button=>button.textContent==="Approve note").click();await ui.settle();
  assert.equal(ui.server.memory[0].status,"approved");assert.match(panel.textContent,/Approved by you/);
  await ui.openAgent(B);await ui.settle();assert.match(panel.textContent,/Prefer concise Arabic summaries/);
  await ui.elements.get("language-ar").click();await ui.settle();
  assert.match(panel.textContent,/معتمدة من قبلك/);assert.doesNotMatch(panel.textContent,/verified|معلومة موثقة/);
  await byTag(ui.elements.get("memory-items"),"button").find(button=>button.textContent==="إزالة من المعرفة المشتركة").click();await ui.settle();
  assert.equal(ui.server.memory[0].status,"rejected");assert.doesNotMatch(ui.elements.get("memory-items").textContent,/Prefer concise/);
  assert.equal(ui.generations().length,0);assert.equal(ui.messages().length,0);
});

test("a saved-memory refresh blocks note mutations until its older read finishes", async () => {
  let holdRead = false, finishRead;
  const ui = await start({fetch: (url, request, respond, server) => {
    if (holdRead && url === PREFIX + "/memory" && request.method === "GET") {
      holdRead = false;
      const snapshot = clone(server.memory);
      return new Promise(resolve => { finishRead = () => resolve(respond({items: snapshot})); });
    }
  }});
  await ui.openAgent(A); await ui.settle();
  ui.server.memory.push({memory_id: "memory_1", kind: "preference", content: "Use concise summaries.", status: "proposed"});
  await ui.loadMemory();
  await typeChat(ui, "Summarize the saved context."); await ui.sendChat(ui.chatState(A)); await ui.settle();
  holdRead = true;
  const panel = ui.elements.get("shared-memory"); panel.open = true; await panel.emit("toggle"); await ui.settle();
  assert.equal(typeof finishRead, "function");
  const approve = byTag(ui.elements.get("memory-items"), "button").find(button => button.textContent === "Approve note");
  assert.equal(approve.disabled, true);
  await approve.click();
  ui.elements.get("memory-kind").value = "knowledge"; ui.elements.get("memory-input").value = "Another saved note.";
  await ui.elements.get("memory-form").requestSubmit(); await ui.settle();
  assert.equal(ui.requests.filter(request => /\/memory(?:\/|$)/.test(request.url) && request.options.method !== "GET").length, 0);
  finishRead(); await ui.settle();
  await byTag(ui.elements.get("memory-items"), "button").find(button => button.textContent === "Approve note").click(); await ui.settle();
  assert.equal(ui.server.memory[0].status, "approved");
  assert.match(ui.elements.get("memory-items").textContent, /Approved by you/);
  assert.equal(ui.server.memory.length, 1);
});

test("reopening shared memory during a note save waits for the mutation before reloading", async () => {
  for (const method of ["PATCH", "POST"]) {
    let finishSave;
    const ui = await start({fetch: (url, request, respond, server) => {
      if (/\/memory(?:\/|$)/.test(url) && request.method === method) {
        const input = JSON.parse(request.body);
        return new Promise(resolve => { finishSave = () => {
          if (method === "PATCH") server.memory[0].status = "approved";
          else server.memory.push({memory_id: "memory_2", ...input, status: "proposed"});
          resolve(respond({}));
        }; });
      }
    }});
    await ui.openAgent(A); await ui.settle();
    ui.server.memory.push({memory_id: "memory_1", kind: "preference", content: "Use concise summaries.", status: "proposed"});
    const panel = ui.elements.get("shared-memory"); panel.open = true; await panel.emit("toggle"); await ui.settle();
    await typeChat(ui, "Summarize the saved context."); await ui.sendChat(ui.chatState(A)); await ui.settle();
    let saving;
    if (method === "PATCH") saving = byTag(ui.elements.get("memory-items"), "button").find(button => button.textContent === "Approve note").click();
    else {
      ui.elements.get("memory-kind").value = "knowledge"; ui.elements.get("memory-input").value = "A second note.";
      saving = ui.proposeMemory();
    }
    await ui.settle(); assert.equal(typeof finishSave, "function");
    panel.open = false; await panel.emit("toggle"); panel.open = true; await panel.emit("toggle"); await ui.settle();
    const reads = () => ui.requests.filter(request => request.url === PREFIX + "/memory" && request.options.method === "GET");
    assert.equal(reads().length, 1, "Reopening the panel must not read a pre-mutation snapshot while a save is pending.");
    finishSave(); await saving; await ui.settle();
    assert.equal(reads().length, 2, "The completed mutation must be followed by a fresh saved-state read.");
    assert.match(ui.elements.get("memory-items").textContent, method === "PATCH" ? /Approved by you/ : /A second note/);
    assert.equal(ui.elements.get("memory-save").disabled, false);
  }
});

test("owner workspace uses the same manual UI through protected APIs without reading or writing credentials", async () => {
  const ui=await start({owner:true,now:NEWS_NOW,newsAgents:[A],feeds:[[A+":en",newsFeed()]]});
  assert.deepEqual(ui.requests.map(r=>r.url),["/v1/workspace/bootstrap","/v1/workspace/config"]);
  const saved=seed(ui);await ui.openAgent(A);await ui.settle();
  assert.equal(ui.generations().length,0);assert.equal(ui.messages().length,0);assert.equal(ui.newsRequests().length,0);
  assert.equal(ui.elements.get("pdf-download").href,"/v1/workspace/reports/"+saved.report_id+"/pdf");
  await openResearch(ui);await byClass(ui.elements.get("report-content"),"news-refresh")[0].click();await ui.settle();
  assert.deepEqual(ui.newsRequests().map(r=>r.options.method),["GET","POST"]);
  await typeChat(ui,"Owner agent question");await ui.sendChat(ui.chatState(A));await ui.settle();
  assert.match(ui.messages()[0].url,/^\/v1\/workspace\/conversations\/conv_[a-f0-9]{32}\/messages\/stream$/);
  await ui.loadMemory();ui.elements.get("memory-kind").value="knowledge";ui.elements.get("memory-input").value="Owner note";await ui.proposeMemory();
  await ui.generateReport(A);await ui.settle();assert.equal(ui.generations().length,1);
  for(const request of ui.requests){assert.match(request.url,/^\/v1\/workspace\//);assert.equal(request.options.headers.Authorization,undefined);assert.equal(request.options.credentials,"same-origin");}
  assert.deepEqual([...ui.storage.keys()],["aihub.language"]);assert.equal([...ui.session.keys()].some(key=>/auth|credential|password/i.test(key)),false);
  await assert.rejects(ui.request("/v1/usage"));await assert.rejects(ui.request("/v1/public/workspace/../../usage"));
  assert.equal(ui.requests.some(r=>r.url.startsWith("/v1/public/")),false);
});

test("news errors distinguish application allowance, provider credits and provider rate limits in both languages", async () => {
  for(const [code,english,arabic]of [
    ["newsdata_daily_credit_limit",/AI HUB has reached its daily NewsData allowance.*midnight in Dubai/,/المنصة إلى حصتها اليومية.*منتصف الليل/],
    ["newsdata_provider_credit_limit",/provider credits are exhausted/,/نفاد رصيد حساب الخدمة/],
    ["newsdata_rate_limited",/source has reached its request limit/,/مصدر الأخبار إلى حدّ الطلبات/]
  ]){
    const failed=newsFeed({status:"provider_error",error:{code,retryable:true,retry_after_seconds:10}});
    const ui=await startNews({now:NEWS_NOW,newsAgents:[A],feeds:[[A+":en",failed],[A+":ar",failed]]});
    assert.match(ui.elements.get("report-content").textContent,english);
    await ui.elements.get("language-ar").click();await ui.settle();assert.match(ui.elements.get("report-content").textContent,arabic);
    assert.equal(ui.newsRequests().every(request=>request.options.method==="GET"),true);
  }
});

test("supplemental web retrieval and article evidence levels are explicit without hiding useful results", async () => {
  const feed=newsFeed({partial:true,items:[newsItem(1,{publisher_text_verified:false}),newsItem(2,{publisher_text_verified:true})],
    retrieval:{newsdata:{status:"complete",raw_count:50,accepted_count:1},web_search:{attempted:true,status:"complete",accepted_count:1}},pagination:{raw_count:50}});
  const ui=await startNews({now:NEWS_NOW,newsAgents:[A],feeds:[[A+":en",feed]]});
  const panel=ui.elements.get("report-content");assert.match(panel.textContent,/Additional web research: 1 qualifying articles/);
  assert.match(panel.textContent,/NewsData metadata.*Publisher page text not checked/);assert.match(panel.textContent,/Publisher page and publication time checked/);
  assert.equal(byClass(panel,"live-news-item").length,2);assert.match(panel.textContent,/50 provider records fetched/);
  assert.equal(ui.generations().length,0);assert.equal(ui.messages().length,0);
});

test("a web fallback search-action violation remains a precise failure despite provider HTTP200", async () => {
  const reference = "news_0123456789abcdef";
  const failed = newsFeed({status: "provider_error", items: [], last_successful_refresh_at: null,
    error: {code: "newsdata_daily_credit_limit", scope: "application", limit_kind: "application_daily_credit"},
    retrieval: {web_search: {attempted: true, status: "error", accepted_count: 0,
      error: {code: "search_call_limit_exceeded", reference, retryable: true, retry_after_seconds: 900,
        provider_status: 200, message: "PRIVATE provider diagnostic", provider_request_id: "private-provider-request"}}}});
  const ui = await startNews({now: NEWS_NOW, newsAgents: [A], feeds: [[A + ":en", failed], [A + ":ar", failed]]});
  const panel = ui.elements.get("report-content");
  assert.match(panel.textContent, /AI HUB has reached its daily NewsData allowance/);
  assert.match(panel.textContent, /Additional web research did not complete.*exceeded the permitted number of search actions.*operation was stopped.*search was not accepted as successful/);
  assert.match(panel.textContent, /Error reference: news_0123456789abcdef/);
  assert.equal(byTag(panel, "bdi").find(node => node.textContent === reference).dir, "ltr");
  assert.doesNotMatch(panel.textContent, /response did not satisfy|Additional web research:|PRIVATE|private-provider-request|HTTP.?200/);
  await ui.elements.get("language-ar").click(); await ui.settle();
  assert.match(panel.textContent, /المنصة إلى حصتها اليومية.*لم يكتمل بحث الويب الإضافي.*تجاوز مزوّد البحث العدد المسموح.*أُوقفت العملية.*لم يُعتمد البحث كنتيجة ناجحة/);
  assert.match(panel.textContent, /مرجع الخطأ: news_0123456789abcdef/);
  assert.doesNotMatch(panel.textContent, /لم يستوفِ رد مزود البحث|بحث الويب الإضافي:|PRIVATE|private-provider-request/);
  assert.equal(ui.newsRequests().every(request => request.options.method === "GET"), true);
  assert.equal(ui.generations().length, 0); assert.equal(ui.messages().length, 0);
});

test("an explicitly requested news refresh can poll for400 seconds without launching another refresh", async () => {
  const ui=await startNews({now:NEWS_NOW,newsAgents:[A],feeds:[[A+":en",newsFeed()]],fetch:(url,request,respond,server)=>{
    if(url.endsWith("/news/refresh")){const loading=newsFeed({status:"loading",refresh_started:true});server.feeds.set(A+":en",loading);return respond(loading);}
  }});
  await byClass(ui.elements.get("report-content"),"news-refresh")[0].click();await ui.settle();
  for(let step=1;step<=200;step++){ui.setNow(NEWS_NOW+step*2000);await ui.advance(2000);}
  assert.equal(ui.newsState(A).pollPaused,false);assert.equal(ui.newsRequests().filter(request=>request.options.method==="POST").length,1);
  ui.server.feeds.set(A+":en",newsFeed({last_successful_refresh_at:new Date(NEWS_NOW+400000).toISOString()}));
  await ui.advance(2000);assert.equal(ui.newsState(A).data.status,"fresh");
  const count=ui.newsRequests().length;await ui.advance(900000);assert.equal(ui.newsRequests().length,count);
});

test("background-only reports retain substantive dated analysis, source categories and PDF access", async () => {
  const ui=await start({now:NEWS_NOW,newsAgents:[A]});const saved=seed(ui);
  Object.assign(saved.result,{status:"background_only",items:[],actual_article_count:0,title:"Background and analysis: synthetic context",
    sections:[{id:"executive_summary",title:"Executive summary",body:"No fresh development was verified within the 12-hour window.\n\n**Substantive background analysis.**",source_ids:[SOURCE_ID]}],
    sources:[{id:SOURCE_ID,title:"Dated opinion source",url:"https://publisher.example/opinion",publisher:"Fixture Publisher",published_at:"2026-10-10",role:"background",source_category:"opinion",publisher_text_verified:true}]});
  await ui.openAgent(A);await ui.settle();await ui.openSaved(saved.report_id,A);await ui.settle();
  const content=ui.elements.get("report-content"),meta=ui.elements.get("report-meta");
  assert.match(meta.textContent,/Analysis based on dated background references/);assert.match(content.textContent,/Substantive background analysis/);
  assert.match(content.textContent,/10 Oct 2026.*Background reference.*Opinion.*Publisher text checked/);
  assert.doesNotMatch(content.textContent,/04:00/);assert.equal(ui.elements.get("pdf-download").hidden,false);
  assert.equal(ui.elements.get("report-notice").hidden,true);assert.equal(ui.generations().length,0);
});

test("application news blocks permit an explicit recheck while provider cooldowns remain enforced", async () => {
  const blocked=newsFeed({status:"provider_error",error:{code:"newsdata_daily_credit_limit",scope:"application",limit_kind:"application_daily_credit",retry_after_seconds:86400,retryable:true}});
  const ui=await startNews({now:NEWS_NOW,newsAgents:[A],feeds:[[A+":en",blocked]],fetch:(url,request,respond)=>url.endsWith("/news/refresh")?respond(newsFeed()):undefined});
  const button=byClass(ui.elements.get("report-content"),"news-refresh")[0];assert.equal(button.disabled,false);
  await ui.advance(900000);assert.equal(ui.newsRequests().length,1,"A cleared UI retry gate must not start any automatic provider work.");
  await button.click();await ui.settle();assert.equal(ui.newsRequests().filter(request=>request.options.method==="POST").length,1);
  assert.equal(ui.newsState(A).data.status,"fresh");
});
