// Run with: node tests/test_ui_news.js
// These checks exercise the shipped renderer with a minimal DOM, without a browser or network.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const staticPath = path.join(__dirname, "../app/static");
const html = fs.readFileSync(path.join(staticPath, "index.html"), "utf8");
const script = fs.readFileSync(path.join(staticPath, "app.js"), "utf8");

class Element {
  constructor(tag = "div") {
    this.tagName = tag.toUpperCase();
    this.nodeType = 1;
    this.childNodes = [];
    this.className = "";
    this.value = "";
    this.attributes = {};
    this.events = {};
    this.classList = {
      add: (...names) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...names])].join(" "); },
      remove: (...names) => { this.className = this.className.split(/\s+/).filter(name => !names.includes(name)).join(" "); },
      contains: name => this.className.split(/\s+/).includes(name),
      toggle: (name, force) => {
        const add = force === undefined ? !this.classList.contains(name) : force;
        this.classList[add ? "add" : "remove"](name);
        return add;
      }
    };
  }
  get children() { return this.childNodes.filter(node => node.nodeType === 1); }
  get textContent() { return this.childNodes.map(node => node.textContent).join(""); }
  set textContent(value) { this.childNodes = [{nodeType: 3, textContent: String(value)}]; }
  set innerHTML(_) { throw new Error("The report renderer must not insert raw HTML."); }
  append(...nodes) {
    for (const node of nodes) {
      const child = typeof node === "string" ? {nodeType: 3, textContent: node} : node;
      child.parentNode = this;
      this.childNodes.push(child);
    }
  }
  replaceChildren(...nodes) { this.childNodes = []; this.append(...nodes); }
  setAttribute(name, value) { this.attributes[name] = String(value); }
  addEventListener(type, listener) { (this.events[type] ||= []).push(listener); }
  async click() { for (const listener of this.events.click || []) await listener({}); }
  remove() {
    if (this.parentNode) this.parentNode.childNodes = this.parentNode.childNodes.filter(node => node !== this);
  }
  scrollIntoView() {}
  focus() {}
  reset() {}
}

function descendants(root, predicate) {
  return root.children.flatMap(child => [...(predicate(child) ? [child] : []), ...descendants(child, predicate)]);
}
const byClass = (root, name) => descendants(root, element => element.classList.contains(name));
const byTag = (root, tag) => descendants(root, element => element.tagName === tag.toUpperCase());

function harness(savedReport = null) {
  const elements = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element()]));
  const body = new Element("body");
  const requests = [];
  let copied = "";
  class TestURL extends URL {
    static createObjectURL() { return "blob:ai-hub-test"; }
    static revokeObjectURL() {}
  }
  const context = {
    URL: TestURL,
    document: {
      body,
      getElementById: id => elements.get(id) || null,
      createElement: tag => new Element(tag),
      createTextNode: text => ({nodeType: 3, textContent: String(text)})
    },
    navigator: {clipboard: {writeText: async value => { copied = value; }}},
    window: {confirm: () => false},
    setTimeout: () => 0,
    fetch: async (url, options) => {
      requests.push({url, options});
      assert.match(url, /^\/v1\/reports\//, "Only fixture report routes may be requested.");
      return {ok: true, text: async () => JSON.stringify(savedReport), blob: async () => ({})};
    }
  };
  const instrumented = script.replace(/\n  fetchHealth\(\);\n\}\)\(\);\s*$/, `
    globalThis.testUI = {displayResult, renderArchive, officialNewsSource,
      setAgents: value => { agents = value; }};
  })();`);
  assert.notEqual(instrumented, script, "Test instrumentation must match the app entry point.");
  vm.runInNewContext(instrumented, context, {filename: "app.js"});
  context.testUI.setAgents([{id: "iran-news", name: "أخبار إيران", number: "01", runtime_ready: true}]);
  return {...context.testUI, elements, requests, body, copied: () => copied};
}

function item(overrides = {}) {
  return {
    headline: "عنوان الخبر الأول",
    summary: "ملخص قصير يوضح التطور الرئيسي والسياق المرتبط به.",
    url: "https://www.aljazeera.net/news/2026/10/20/example-article",
    published_date: "2026-10-20",
    published_day_dubai: "2026-10-20",
    classification: "publication_window_report",
    ...overrides
  };
}
function report(overrides = {}) {
  return {
    agent_id: "iran-news", report_id: "rpt_ui_fixture", items: [item()], background_items: [],
    item_count: 1, target_count: null, shortfall: null, bounded_item_limit: 20,
    generated_at: "2026-10-20T08:00:00+04:00",
    coverage_window: {start: "2026-10-20T00:00:00+04:00", end: "2026-10-20T23:59:59+04:00"},
    response: "## التقرير الأصلي\n\n[المصدر](https://www.aljazeera.net/news/2026/10/20/example-article)",
    ...overrides
  };
}

test("news presents a headline, summary, publisher, publication date and direct official link", () => {
  const ui = harness();
  ui.displayResult(report());
  const body = ui.elements.get("responseBody");
  assert.equal(byClass(body, "news-item").length, 1);
  assert.equal(byClass(body, "news-headline")[0].textContent, item().headline);
  assert.equal(byClass(body, "news-summary")[0].textContent, item().summary);
  assert.equal(byClass(body, "news-publisher")[0].textContent, "الجزيرة");
  assert.equal(byTag(body, "time")[0].dateTime, "2026-10-20");
  const link = byClass(body, "news-source-link")[0];
  assert.equal(link.href, item().url);
  assert.equal(link.target, "_blank");
  assert.equal(link.rel, "noopener noreferrer");
  assert.match(link.textContent, /فتح الخبر على الموقع الرسمي/);
  assert.equal(byClass(body, "news-coverage").length, 1);
  assert.equal(byTag(body, "details").length, 1);
  assert.equal(byTag(body, "details")[0].attributes.open, undefined);
});

test("empty current news and a previous-day item remain separate without a fixed target", () => {
  const ui = harness();
  ui.displayResult(report({items: [], item_count: 0, background_items: [item({classification: "prior_day_background", published_day_dubai: "2026-10-19"})]}));
  const body = ui.elements.get("responseBody");
  assert.equal(byClass(body, "news-list").length, 0);
  assert.equal(byClass(body, "news-empty").length, 1);
  assert.equal(byClass(body, "news-background").length, 1);
  assert.equal(byClass(body, "news-count")[0].textContent, "عدد الأخبار: 0");
  assert.equal(byClass(body, "news-source-link").length, 1);
  assert.equal(byClass(body, "news-context-label")[0].textContent, "من اليوم السابق");
  assert.doesNotMatch(body.textContent, /0\s*[\/]\s*6|ستة/);
});

test("only approved publisher domains and subdomains produce official source links", () => {
  const ui = harness();
  for (const host of ["ft.com", "theatlantic.com", "politico.com", "politico.eu", "wsj.com", "telegraph.co.uk", "aljazeera.com", "aljazeera.net", "www.ft.com"]) {
    assert.ok(ui.officialNewsSource("https://" + host + "/article/example"), host);
  }
  for (const url of ["javascript:alert(1)", "data:text/html,example", "//ft.com/article", "https://ft.com.evil.example/article", "https://ft.com@evil.example/article", "https://person:secret@ft.com/article", "https://ft.com:8443/article", "https://ft.com/", "https://unapproved.example/article", "https://ft.com\\@evil.example/article", "https://ft.com/\narticle"]) {
    assert.equal(ui.officialNewsSource(url), null, url);
  }
});

test("untrusted news text stays text and unsafe URLs never become clickable", () => {
  const ui = harness();
  const headline = '<img src=x onerror="alert(1)"> عنوان';
  const summary = '<script>alert(1)</script> [اضغط](javascript:alert(1))';
  ui.displayResult(report({items: [item({headline, summary, url: "javascript:alert(1)"})]}));
  const body = ui.elements.get("responseBody");
  assert.equal(byClass(body, "news-headline")[0].textContent, headline);
  assert.equal(byClass(body, "news-summary")[0].textContent, summary);
  assert.equal(byTag(body, "a").length, 0);
  assert.equal(byTag(body, "img").length, 0);
  assert.equal(byTag(body, "script").length, 0);
  assert.equal(byClass(body, "news-link-unavailable").length, 1);
});

test("large explicit requests keep their target and show the shortfall under the shared ceiling", () => {
  const ui = harness();
  ui.displayResult(report({items: Array.from({length: 24}, (_, n) => item({headline: "خبر " + n})), background_items: [item()], target_count: 27, item_count: 24}));
  const body = ui.elements.get("responseBody");
  assert.equal(byClass(body, "news-item").length, 20);
  assert.equal(byClass(body, "news-count")[0].textContent, "عدد الأخبار: 20 من 27 المطلوبة");
  assert.match(byClass(body, "news-coverage-note")[0].textContent, /7 خبرًا/);
  assert.equal(byClass(body, "news-background").length, 0);
});

test("non-news Markdown retains headings, prose and links when switching from news", () => {
  const ui = harness();
  ui.displayResult(report());
  ui.displayResult({response: "## مذكرة\n\nنص **مهم** مع [المصدر](https://example.org/document)."});
  const body = ui.elements.get("responseBody");
  assert.equal(body.classList.contains("news-reading"), false);
  assert.equal(body.dir, "auto");
  assert.equal(byClass(body, "news-item").length, 0);
  assert.equal(byTag(body, "h3")[0].textContent, "مذكرة");
  assert.equal(byTag(body, "strong")[0].textContent, "مهم");
  assert.equal(byTag(body, "a")[0].href, "https://example.org/document");
});

test("opening archived news uses the same renderer while copy and download preserve the original report", async () => {
  const data = report();
  const saved = {report_id: data.report_id, agent_id: "iran-news", status: "succeeded", result: data, estimated_cost_usd: 0.07};
  const ui = harness(saved);
  ui.renderArchive([{...saved, task: "أخبار إيران", created_at: data.generated_at}]);
  await byTag(ui.elements.get("archiveList"), "button")[0].click();
  assert.equal(byClass(ui.elements.get("responseBody"), "news-source-link")[0].href, item().url);
  await ui.elements.get("copyBtn").click();
  assert.equal(ui.copied(), data.response);
  await ui.elements.get("downloadBtn").click();
  assert.equal(ui.requests[0].url, "/v1/reports/rpt_ui_fixture");
  assert.equal(ui.requests[1].url, "/v1/reports/rpt_ui_fixture/download?format=md");
});
