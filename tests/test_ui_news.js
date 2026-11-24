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
  setAttribute(name, value) {
    this.attributes[name] = String(value);
    if(name === "class") this.className=String(value);
    if(["id","dir","lang","placeholder","value"].includes(name)) this[name]=String(value);
    if(name === "disabled") this.disabled=true;
    if(name === "checked") this.checked=true;
  }
  getAttribute(name) { return this.attributes[name] ?? null; }
  querySelectorAll(selector) {
    if(/^\[[\w-]+\]$/.test(selector)) return descendants(this,el=>el.getAttribute(selector.slice(1,-1))!==null);
    if(selector.startsWith(".")) return descendants(this,el=>el.classList.contains(selector.slice(1)));
    return descendants(this,el=>el.tagName===selector.toUpperCase());
  }
  addEventListener(type, listener) { (this.events[type] ||= []).push(listener); }
  async click() { for (const listener of this.events.click || []) await listener({}); }
  remove() {
    if (this.parentNode) this.parentNode.childNodes = this.parentNode.childNodes.filter(node => node !== this);
  }
  scrollIntoView() {}
  focus() {}
  reset() {
    for(const el of descendants(this,()=>true)) {
      if(["INPUT","TEXTAREA"].includes(el.tagName)) { el.value=el.getAttribute("value") || ""; el.checked=el.getAttribute("checked")!==null; }
      if(el.tagName==="SELECT") el.value=el.children[0]?.value || "";
    }
  }
}

function descendants(root, predicate) {
  return root.children.flatMap(child => [...(predicate(child) ? [child] : []), ...descendants(child, predicate)]);
}
const byClass = (root, name) => descendants(root, element => element.classList.contains(name));
const byTag = (root, tag) => descendants(root, element => element.tagName === tag.toUpperCase());

function harness(savedReport = null, options = {}) {
  const elements = new Map(), tree = new Element("document"), stack = [tree];
  const voidTags = new Set(["META","LINK","INPUT","BR","HR","IMG"]);
  for(const match of html.matchAll(/<\/?([a-zA-Z][a-zA-Z0-9-]*)\b([^>]*)>|([^<]+)/g)) {
    if(match[3]!==undefined) { stack.at(-1).append(match[3]); continue; }
    if(match[0].startsWith("</")) { if(stack.length>1) stack.pop(); continue; }
    const element = new Element(match[1]);
    for(const attribute of match[2].matchAll(/([a-zA-Z_][\w-]*)(?:="([^"]*)")?/g)) element.setAttribute(attribute[1],attribute[2] ?? "");
    if(element.id) elements.set(element.id,element);
    stack.at(-1).append(element);
    if(!voidTags.has(element.tagName)) stack.push(element);
  }
  const body = descendants(tree,el=>el.tagName==="BODY")[0];
  const document = {body,documentElement:descendants(tree,el=>el.tagName==="HTML")[0],title:"",
    getElementById:id=>elements.get(id) || null,createElement:tag=>new Element(tag),
    createTextNode:text=>({nodeType:3,textContent:String(text)}),querySelectorAll:selector=>tree.querySelectorAll(selector)};
  const requests=[], storage=new Map(options.language ? [["aihub.language",options.language]] : []), listeners=new Map();
  const chatCalls={create:0,login:0,logout:0,language:0,context:0}; let chatConfig,copied="", uuid=0;
  class TestURL extends URL { static createObjectURL(){return "blob:ai-hub-test";} static revokeObjectURL(){} }
  const response = (data, status=200) => ({ok:status>=200&&status<300,status,text:async()=>JSON.stringify(data),blob:async()=>({})});
  const context={URL:TestURL,document,AbortController,crypto:{randomUUID:()=>"ui-fixture-"+(++uuid)},
    btoa:value=>Buffer.from(value,"binary").toString("base64"),
    CustomEvent:class {constructor(type,init={}){this.type=type;this.detail=init.detail;}},
    navigator:{clipboard:{writeText:async value=>{copied=value;}}},
    window:{confirm:()=>false,location:{origin:"https://ai-hub.test"},
      localStorage:{getItem:key=>storage.get(key) ?? null,setItem:(key,value)=>storage.set(key,String(value))},
      addEventListener:(type,listener)=>{if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(listener);},
      dispatchEvent:event=>{for(const listener of listeners.get(event.type)||[])listener(event);},
      AIHubChat:{create:config=>{chatCalls.create++;chatConfig=config;return {login:()=>chatCalls.login++,logout:()=>chatCalls.logout++,setLanguage:()=>chatCalls.language++,refreshReportContext:()=>chatCalls.context++};}}},
    setTimeout:()=>0,
    fetch:async(url,requestOptions)=>{
      requests.push({url,options:requestOptions});
      if(options.fetch) return options.fetch(url,requestOptions,response);
      assert.match(url,/^\/v1\/reports\//,"Only fixture report routes may be requested.");
      return response(savedReport);
    }};
  const instrumented=script.replace(/\n  fetchHealth\(\);\n\}\)\(\);\s*$/, `
    globalThis.testUI={displayResult,renderArchive,renderSchedules,officialNewsSource,chooseAgent,
      login,logout,runTask,saveSchedule,apiJson,getCurrentReport,showWorkspace,makeError,
      setAgents:value=>{agents=value;},
      setAuthenticated:()=>{authHeader="Basic fixture";sessionVersion++;}};
  })();`);
  assert.notEqual(instrumented,script,"Test instrumentation must match the app entry point.");
  vm.createContext(context);
  vm.runInContext(fs.readFileSync(path.join(staticPath,"i18n.js"),"utf8"),context,{filename:"i18n.js"});
  vm.runInContext(instrumented,context,{filename:"app.js"});
  context.testUI.setAgents([{id:"iran-news",name:"أخبار إيران",number:"01",runtime_ready:true}]);
  return {...context.testUI,elements,requests,body,document,storage,chatCalls,chatConfig,context,
    I18n:context.window.AIHubI18n,copied:()=>copied,response};
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
    item_count: 1, target_count: null, shortfall: null, bounded_item_limit: 50,
    generated_at: "2026-10-20T08:00:00+04:00",
    coverage_window: {start: "2026-10-20T00:00:00+04:00", end: "2026-10-20T23:59:59+04:00"},
    response: "## التقرير الأصلي\n\n[المصدر](https://www.aljazeera.net/news/2026/10/20/example-article)",
    ...overrides
  };
}

function publisherCoverage(counts = {"Al Jazeera": {item_count: 1}}) {
  return [
    ["Financial Times",["ft.com"]],["The Atlantic",["theatlantic.com"]],
    ["Politico",["politico.com","politico.eu"]],["The Wall Street Journal",["wsj.com"]],
    ["The Daily Telegraph",["telegraph.co.uk"]],["Al Jazeera",["aljazeera.com","aljazeera.net"]]
  ].map(([publisher,domains])=>({publisher,domains,item_count:0,background_count:0,...counts[publisher]}));
}

test("publisher coverage is a collapsed bilingual summary of all six actual counts in one report", async () => {
  const ui=harness();ui.displayResult(report({publisher_coverage:publisherCoverage()}));
  const body=ui.elements.get("responseBody");
  let coverage=byClass(body,"news-source-coverage")[0];
  assert.equal(coverage.tagName,"DETAILS");assert.equal(coverage.getAttribute("open"),null);
  assert.equal(coverage.dir,"rtl");assert.equal(coverage.lang,"ar");
  assert.equal(byTag(coverage,"summary")[0].textContent,"المصادر في هذا التقرير");
  assert.deepEqual(byClass(coverage,"news-publisher-name").map(el=>el.textContent),
    ["Financial Times","The Atlantic","Politico","The Wall Street Journal","The Daily Telegraph","الجزيرة"]);
  assert.equal(byClass(coverage,"news-publisher-row").length,6);
  assert.equal(byClass(coverage,"news-publisher-item-count").at(-1).textContent,"أخبار الفترة: 1");
  assert.equal(byClass(coverage,"news-publisher-background-count").at(-1).textContent,"السياق الإضافي: 0");
  assert.equal(byClass(coverage,"news-publisher-missing").length,5);
  assert.equal(byClass(coverage,"news-publisher-missing")[0].textContent,"لم يُدرج خبر مستوفٍ للشروط من هذا المصدر في هذا التقرير.");
  await ui.elements.get("languageEN").click();coverage=byClass(body,"news-source-coverage")[0];
  assert.equal(coverage.dir,"ltr");assert.equal(coverage.lang,"en");
  assert.equal(byTag(coverage,"summary")[0].textContent,"Sources in this report");
  assert.equal(byClass(coverage,"news-publisher-name").at(-1).textContent,"Al Jazeera");
  assert.equal(byClass(coverage,"news-publisher-item-count").at(-1).textContent,"News in period: 1");
  assert.equal(byClass(coverage,"news-publisher-missing")[0].textContent,"No eligible article was included for this publisher in this report.");
  assert.equal(byClass(body,"news-report-title").length,1);
  assert.equal(byClass(body,"news-item").length,1);assert.equal(byClass(body,"news-source-link").length,1);
  assert.equal(ui.requests.length,0,"Expanding coverage or changing labels needs no publisher request.");
});

test("publisher coverage separates background counts from period news and does not claim missing publications", () => {
  const ui=harness(null,{language:"en"});
  ui.displayResult(report({items:[],item_count:0,
    background_items:[item({url:"https://www.ft.com/content/synthetic-previous-day",classification:"prior_day_background",published_day_dubai:"2026-10-19"})],
    publisher_coverage:publisherCoverage({"Financial Times":{background_count:1}})}));
  const body=ui.elements.get("responseBody"),coverage=byClass(body,"news-source-coverage")[0];
  const ft=byClass(coverage,"news-publisher-row")[0];
  assert.equal(byClass(ft,"news-publisher-item-count")[0].textContent,"News in period: 0");
  assert.equal(byClass(ft,"news-publisher-background-count")[0].textContent,"Additional context: 1");
  assert.equal(byClass(ft,"news-publisher-missing").length,0,"An included background article is not absent from the report.");
  assert.equal(byClass(coverage,"news-publisher-missing").length,5);
  assert.equal(byClass(body,"news-count")[0].textContent,"News items: 0");
  assert.equal(byClass(body,"news-background").length,1);
  assert.doesNotMatch(coverage.textContent,/not searched|did not publish|no articles? (?:exists?|published)/i);
});

test("legacy and incomplete publisher metadata do not invent coverage or replace the existing report", () => {
  const ui=harness();
  for(const coverage of [undefined,null,[],publisherCoverage().slice(0,5),
    publisherCoverage().map((row,index)=>index===0 ? {...row,item_count:"0"} : row),
    publisherCoverage().map((row,index)=>index===0 ? {...row,publisher:"<img src=x onerror=alert(1)>"} : row)]){
    ui.displayResult(report({publisher_coverage:coverage}));
    const body=ui.elements.get("responseBody");
    assert.equal(byClass(body,"news-source-coverage").length,0);
    assert.equal(byClass(body,"news-headline")[0].textContent,item().headline);
    assert.equal(byClass(body,"news-source-link")[0].href,item().url);
    assert.equal(byClass(body,"news-notes").length,1);
  }
});

test("archived publisher coverage uses the same renderer while copy and download retain the saved report", async () => {
  const data=report({publisher_coverage:publisherCoverage()});
  const saved={report_id:data.report_id,agent_id:"iran-news",status:"succeeded",result:data};
  const ui=harness(saved);ui.setAuthenticated();
  ui.renderArchive([{...saved,task:"Synthetic archived report",created_at:data.generated_at}]);
  await byTag(ui.elements.get("archiveList"),"button")[0].click();
  const coverage=byClass(ui.elements.get("responseBody"),"news-source-coverage")[0];
  assert.equal(byClass(coverage,"news-publisher-row").length,6);
  assert.equal(byClass(coverage,"news-publisher-item-count").at(-1).textContent,"أخبار الفترة: 1");
  await ui.elements.get("copyBtn").click();assert.equal(ui.copied(),data.response);
  await ui.elements.get("downloadBtn").click();
  assert.equal(ui.requests[0].url,"/v1/reports/rpt_ui_fixture");
  assert.equal(ui.requests[1].url,"/v1/reports/rpt_ui_fixture/download?format=md");
});

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
  assert.equal(byClass(body, "news-live-coverage").length, 0, "Legacy article results have no invented coverage type.");
});

test("live coverage is labeled in Arabic and English without changing its headline, date or official link", async () => {
  const live = item({source_kind:"live_coverage", headline:"تغطية اختبار افتراضية من طهران",
    url:"https://www.aljazeera.com/news/liveblog/2026/10/20/tehran-synthetic-preview"});
  const ui = harness();
  ui.displayResult(report({items:[live,item({source_kind:"article"})],item_count:2}));
  const body=ui.elements.get("responseBody");
  assert.equal(byClass(body,"news-item").length,2);
  assert.equal(byClass(body,"news-live-coverage").length,1);
  assert.equal(byClass(body,"news-live-coverage")[0].textContent,"تغطية حية");
  assert.equal(byClass(body,"news-headline")[0].textContent,live.headline);
  assert.equal(byClass(body,"news-summary")[0].textContent,live.summary);
  assert.equal(byTag(body,"time")[0].dateTime,"2026-10-20");
  let link=byClass(body,"news-source-link")[0];
  assert.equal(link.href,live.url);assert.equal(link.target,"_blank");assert.equal(link.rel,"noopener noreferrer");
  await ui.elements.get("languageEN").click();
  assert.equal(byClass(body,"news-live-coverage")[0].textContent,"Live coverage");
  assert.equal(byClass(body,"news-headline")[0].textContent,live.headline);
  assert.equal(byClass(body,"news-count")[0].textContent,"News items: 2");
  link=byClass(body,"news-source-link")[0];
  assert.equal(link.href,live.url);assert.match(link.textContent,/Read on the publisher’s website/);
  assert.equal(ui.requests.length,0,"Changing a coverage label must not retrieve new news.");
});

test("an archived previous-day live page keeps its distinct background label and original downloadable report", async () => {
  const live=item({source_kind:"live_coverage",classification:"prior_day_background",
    published_day_dubai:"2026-10-19",published_date:"2026-10-19",
    url:"https://www.aljazeera.net/news/liveblog/2026/10/19/tehran-synthetic-preview"});
  const data=report({items:[],item_count:0,background_items:[live]});
  const saved={report_id:data.report_id,agent_id:"iran-news",status:"succeeded",result:data};
  const ui=harness(saved,{language:"en"});ui.setAuthenticated();
  ui.renderArchive([{...saved,task:"Synthetic archived coverage",created_at:data.generated_at}]);
  await byTag(ui.elements.get("archiveList"),"button")[0].click();
  const body=ui.elements.get("responseBody"),background=byClass(body,"news-background")[0];
  assert.equal(byClass(body,"news-list").length,0);
  assert.equal(byClass(background,"news-item").length,1);
  assert.equal(byClass(background,"news-live-coverage")[0].textContent,"Live coverage");
  assert.equal(byClass(background,"news-context-label").length,2);
  assert.match(byClass(background,"news-context-label")[1].textContent,/previous-day report/i);
  assert.equal(byClass(background,"news-source-link")[0].href,live.url);
  await ui.elements.get("copyBtn").click();assert.equal(ui.copied(),data.response);
  await ui.elements.get("downloadBtn").click();
  assert.equal(ui.requests[1].url,"/v1/reports/rpt_ui_fixture/download?format=md");
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
  ui.displayResult(report({items: Array.from({length: 54}, (_, n) => item({headline: "خبر " + n})), background_items: [item()], target_count: 57, item_count: 54}));
  const body = ui.elements.get("responseBody");
  assert.equal(byClass(body, "news-item").length, 50);
  assert.equal(byClass(body, "news-count")[0].textContent, "عدد الأخبار: 50 من 57 المطلوبة");
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
  ui.setAuthenticated();
  ui.renderArchive([{...saved, task: "أخبار إيران", created_at: data.generated_at}]);
  await byTag(ui.elements.get("archiveList"), "button")[0].click();
  assert.equal(byClass(ui.elements.get("responseBody"), "news-source-link")[0].href, item().url);
  await ui.elements.get("copyBtn").click();
  assert.equal(ui.copied(), data.response);
  await ui.elements.get("downloadBtn").click();
  assert.equal(ui.requests[0].url, "/v1/reports/rpt_ui_fixture");
  assert.equal(ui.requests[1].url, "/v1/reports/rpt_ui_fixture/download?format=md");
});

const fixtureAgents = JSON.parse(fs.readFileSync(path.join(__dirname,"../agents.json"),"utf8")).agents
  .map(agent=>({...agent,runtime_ready:true,skill_ids:agent.skills.map(skill=>skill.id)}));
function dashboardFixture(url, respond) {
  if(url==="/v1/agents") return respond({agents:fixtureAgents});
  if(url==="/v1/usage") return respond({daily:{attempts:2,limit:50,remaining:48,estimated_cost_usd:0.24,budget_usd:5},monthly:{estimated_cost_usd:1.2,budget_usd:100}});
  if(url==="/v1/system") return respond({model_configured:true,search_configured:true,pricing_configured:true,scheduler_running:true});
  if(url.startsWith("/v1/reports?")) return respond({reports:[]});
  if(url==="/v1/schedules") return respond({schedules:[]});
  if(url.startsWith("/v1/schedule-occurrences?")) return respond({occurrences:[]});
  assert.fail("Unexpected fixture request: "+url);
}

test("English translates every static chrome label and all 7 active agents, with only language persisted", () => {
  const ui=harness(null,{language:"en"});
  assert.equal(ui.I18n.getLanguage(),"en");
  assert.equal(ui.document.documentElement.lang,"en");
  assert.equal(ui.document.documentElement.dir,"ltr");
  for(const attribute of ["data-i18n","data-i18n-placeholder","data-i18n-aria-label","data-i18n-title"]){
    for(const element of ui.document.querySelectorAll("["+attribute+"]")){
      const key=element.getAttribute(attribute);
      assert.ok(ui.I18n.has(key),"Missing translation: "+key);
      assert.doesNotMatch(ui.I18n.t(key),/[\u0600-\u06ff]/,"Arabic remains in English chrome: "+key);
      if(attribute!=="data-i18n")assert.equal(element.getAttribute(attribute.slice(10)),ui.I18n.t(key));
    }
  }
  assert.equal(fixtureAgents.length,7);
  ui.setAgents(fixtureAgents);
  for(const agent of fixtureAgents){
    assert.ok(ui.I18n.has("agent."+agent.id+".name"));
    assert.ok(ui.I18n.has("agent."+agent.id+".description"));
    ui.chooseAgent(agent.id);
    assert.doesNotMatch(ui.elements.get("selectedName").textContent+ui.elements.get("selectedDescription").textContent,/[\u0600-\u06ff]/);
  }
  assert.equal(ui.I18n.t("chat.send"),"Send");
  assert.equal(ui.I18n.t("chat.title"),"AI HUB Chat");
  ui.I18n.setLanguage("ar");
  assert.equal(ui.document.documentElement.dir,"rtl");
  assert.equal(ui.elements.get("languageAR").getAttribute("aria-pressed"),"true");
  assert.deepEqual([...ui.storage],[['aihub.language','ar']]);
  assert.equal(ui.requests.length,0);
});

test("switching UI language preserves an open Arabic report, its attachment identity and task drafts without a request", async () => {
  const ui=harness();ui.setAuthenticated();ui.chooseAgent("iran-news");
  ui.elements.get("task").value="مسودة مهمة خاصة";
  ui.elements.get("scheduleTask").value="مسودة جدول خاص";
  const data=report({language:"ar"});ui.displayResult(data);
  const requestCount=ui.requests.length;
  await ui.elements.get("languageEN").click();
  const body=ui.elements.get("responseBody");
  assert.equal(body.dir,"rtl");assert.equal(body.lang,"ar");
  assert.equal(byClass(body,"news-headline")[0].textContent,item().headline);
  assert.equal(byClass(body,"news-summary")[0].textContent,item().summary);
  assert.equal(byClass(body,"news-publisher")[0].textContent,"Al Jazeera");
  assert.equal(byClass(body,"news-count")[0].textContent,"News items: 1");
  assert.equal(byClass(body,"news-report-intro")[0].dir,"ltr");
  assert.equal(ui.getCurrentReport().id,data.report_id);
  assert.equal(ui.elements.get("task").value,"مسودة مهمة خاصة");
  assert.equal(ui.elements.get("scheduleTask").value,"مسودة جدول خاص");
  assert.equal(ui.elements.get("scheduleLanguage").value,"en");
  await ui.elements.get("copyBtn").click();assert.equal(ui.copied(),data.response);
  await ui.elements.get("languageAR").click();
  assert.equal(byClass(body,"news-count")[0].textContent,"عدد الأخبار: 1");
  assert.equal(ui.requests.length,requestCount);
  assert.equal(ui.chatCalls.language,0);
});

test("an incomplete follow-up and separate event dates are visible and localized", () => {
  const ui=harness(null,{language:"en"});
  ui.displayResult(report({language:"en",items:[item({headline:"A current article about an earlier event",summary:"Synthetic summary.",event_date:"2026-10-18",event_timing_relative_to_window:"before_window"})],web_search:{research_incomplete:true,failed_request_count:1}}));
  const body=ui.elements.get("responseBody");
  assert.match(byClass(body,"news-research-warning")[0].textContent,/additional search could not complete.*coverage remains incomplete/);
  assert.match(byClass(body,"news-event-date")[0].textContent,/Event date reported in research notes: 18 October 2026/);
  assert.equal(byClass(body,"news-list").length,1);
  assert.equal(byClass(body,"news-background").length,0);
  assert.equal(body.dir,"ltr");
  ui.I18n.setLanguage("ar");
  assert.match(byClass(body,"news-research-warning")[0].textContent,/تعذر إكمال البحث الاستكمالي/);
  assert.equal(body.dir,"ltr");
});

test("maintenance console links the owner workspaces without a separate chat destination and logout clears private state", async () => {
  const sources=[...html.matchAll(/<script src="([^"]+)"/g)].map(match=>match[1]);
  assert.deepEqual(sources,["/static/i18n.js?v=2.1.1","/static/app.js?v=2.1.1"]);
  const ui=harness(null,{language:"en",fetch:(url,_options,respond)=>dashboardFixture(url,respond)});
  assert.equal(ui.chatCalls.create,0);assert.equal(ui.chatCalls.login,0);assert.equal(ui.requests.length,0);
  assert.equal(ui.getCurrentReport(),null);
  ui.elements.get("username").value="fixture-user";ui.elements.get("password").value="fixture-password";
  await ui.login();
  assert.equal(ui.chatCalls.login,0);
  assert.equal(ui.elements.get("password").value,"");
  assert.equal(ui.elements.get("workspaceTabs").classList.contains("hidden"),false);
  assert.equal(ui.elements.get("selectedName").textContent,"Global Geopolitical Briefing");
  assert.match(ui.elements.get("schedulerNotice").textContent,/scheduler is running/);
  ui.displayResult(report({language:"ar"}));ui.showWorkspace("chat");
  assert.equal(ui.getCurrentReport().id,"rpt_ui_fixture");
  assert.equal(ui.elements.get("chatView"),undefined);
  assert.match(html,/href="\/admin\/agents"/);
  assert.doesNotMatch(html,/id="chatTab"/);
  ui.elements.get("task").value="private draft";ui.elements.get("scheduleTask").value="private schedule";
  ui.logout();
  assert.equal(ui.chatCalls.logout,0);assert.equal(ui.getCurrentReport(),null);
  for(const id of ["responseBody","archiveList","scheduleList","selectedName","selectedDescription"])assert.equal(ui.elements.get(id).textContent,"");
  for(const id of ["username","password","task","scheduleTask"])assert.equal(ui.elements.get(id).value,"");
  assert.equal(ui.elements.get("reportsView").classList.contains("hidden"),false);
  assert.equal(ui.elements.get("workspaceTabs").classList.contains("hidden"),true);
  assert.equal(ui.I18n.getLanguage(),"en");assert.deepEqual([...ui.storage],[['aihub.language','en']]);
  const count=ui.requests.length;
  await assert.rejects(ui.apiJson("/v1/chat/conversations"),error=>error.status===401);
  assert.equal(ui.requests.length,count);
  assert.equal(ui.context.window.authHeader,undefined);
});

test("the maintenance request closure prevents cross-origin auth, ignores auth overrides and rejects stale session responses", async () => {
  let release;
  const ui=harness(null,{fetch:async(url,options,respond)=>{
    if(url==="/v1/chat/conversations/pending")return new Promise(resolve=>{release=()=>resolve(respond({messages:[{content:"private response"}]}));});
    return respond({conversations:[]});
  }});
  ui.setAuthenticated();
  await assert.rejects(ui.apiJson("https://unapproved.test/v1/chat/conversations"),error=>error.status===400);
  assert.equal(ui.requests.length,0);
  await ui.apiJson("/v1/chat/conversations","POST",{title:"Fixture"},{Authorization:"unsafe override","Idempotency-Key":"fixture-key"});
  assert.equal(ui.requests[0].options.headers.Authorization,"Basic fixture");
  assert.equal(ui.requests[0].options.headers["Idempotency-Key"],"fixture-key");
  assert.equal(ui.requests[0].url,"/v1/chat/conversations");
  const pending=ui.apiJson("/v1/chat/conversations/pending");
  const signal=ui.requests.at(-1).options.signal;
  ui.logout();assert.equal(signal.aborted,true);release();
  await assert.rejects(pending,error=>error.code==="session_ended");
  assert.doesNotMatch(ui.body.textContent,/private response/);
});

test("manual report language is explicit and changing it starts a distinct idempotent attempt", async () => {
  const ui=harness(null,{fetch:async(url,options,respond)=>{
    if(url.endsWith("/run"))throw new Error("Synthetic network failure");
    return dashboardFixture(url,respond);
  }});
  ui.setAuthenticated();ui.chooseAgent("iran-news");ui.elements.get("task").value="Fixture news request";
  await ui.runTask();await ui.runTask();ui.I18n.setLanguage("en");await ui.runTask();
  const runs=ui.requests.filter(request=>request.url.endsWith("/run"));
  assert.equal(runs.length,3);
  assert.deepEqual(runs.map(request=>JSON.parse(request.options.body).language),["ar","ar","en"]);
  assert.equal(runs[0].options.headers["Idempotency-Key"],runs[1].options.headers["Idempotency-Key"]);
  assert.notEqual(runs[1].options.headers["Idempotency-Key"],runs[2].options.headers["Idempotency-Key"]);
  assert.match(ui.elements.get("runNotice").textContent,/connection|confirm/i);
});

test("schedule creation follows the selected language and editing keeps the saved language", async () => {
  const ui=harness(null,{language:"en",fetch:(url,options,respond)=>{
    if(url.startsWith("/v1/schedules")&&options.method!=="GET")return respond({schedule_id:"fixture-schedule"});
    return dashboardFixture(url,respond);
  }});
  ui.setAuthenticated();ui.chooseAgent("iran-news");ui.I18n.setLanguage("ar");ui.I18n.setLanguage("en");
  ui.elements.get("scheduleAgent").value="iran-news";ui.elements.get("scheduleTask").value="Synthetic daily task";ui.elements.get("scheduleTimes").value="08:00, 13:00";
  await ui.saveSchedule({preventDefault(){}});
  const created=ui.requests.find(request=>request.options.method==="POST");
  assert.equal(JSON.parse(created.options.body).language,"en");
  ui.renderSchedules([{schedule_id:"fixture-schedule",name:"Saved schedule",language:"ar",agent_id:"iran-news",task:"المهمة الأصلية",times:["08:00"],enabled:false}]);
  assert.match(ui.elements.get("scheduleList").textContent,/Report language: Arabic/);
  await byTag(ui.elements.get("scheduleList"),"button")[0].click();
  assert.equal(ui.elements.get("scheduleLanguage").value,"ar");
  ui.I18n.setLanguage("ar");ui.I18n.setLanguage("en");
  assert.equal(ui.elements.get("scheduleLanguage").value,"ar");
  assert.equal(ui.elements.get("scheduleTask").value,"المهمة الأصلية");
  await ui.saveSchedule({preventDefault(){}});
  const changed=ui.requests.find(request=>request.options.method==="PATCH");
  assert.equal(JSON.parse(changed.options.body).language,"ar");
});

test("known backend errors are localized without showing raw service messages", () => {
  const ui=harness(null,{language:"en"});
  const error=ui.makeError({detail:{code:"chat_history_changed",message:"RAW_PRIVATE_SERVER_MESSAGE"}},409);
  assert.equal(error.code,"chat_history_changed");assert.equal(error.status,409);
  assert.match(error.message,/conversation changed/);assert.doesNotMatch(error.message,/RAW_PRIVATE/);
  ui.I18n.setLanguage("ar");
  assert.match(ui.makeError({detail:{code:"invalid_chat_response"}},502).message,/لم يصل رد صالح/);
});

function newsdataReport(overrides={}) {
  return report({provider:"newsdata",language:"ar",task:"iran war",report_id:"rpt_newsdata_page_1",
    items:[item({article_id:"fixture-1",publisher:"Reuters",url:"https://www.reuters.com/world/middle-east/fixture-article",
      headline_en:"Original English headline",summary_en:"Original English description.",
      headline_ar:"عنوان مترجم إلى العربية",summary_ar:"ملخص الخبر مترجم إلى العربية.",translation_status:"translated",
      published_date:"2026-10-20T03:00:00+00:00"})],
    pagination:{has_more:true,page_index:1,page_size:10,parent_report_id:null,root_report_id:"rpt_newsdata_page_1",total_results:127},
    newsdata:{provider_total_results:127},freshness:{plan:"free"},
    response_ar:"## تقرير عربي محفوظ\n\nعنوان مترجم إلى العربية",
    response_en:"## Saved English report\n\nOriginal English headline",...overrides});
}

test("NewsData displays any validated publisher and switches saved translations instantly", async () => {
  const ui=harness();ui.setAuthenticated();ui.chooseAgent("iran-news");const data=newsdataReport();ui.displayResult(data);
  const body=ui.elements.get("responseBody");
  assert.equal(byClass(body,"news-headline")[0].textContent,"عنوان مترجم إلى العربية");
  assert.equal(byClass(body,"news-publisher")[0].textContent,"Reuters");
  assert.equal(byClass(body,"news-source-link")[0].href,data.items[0].url);
  assert.equal(byClass(body,"news-source-coverage").length,0,"Iran must not show the old six-publisher list.");
  assert.match(body.textContent,/12|١٢/);
  assert.match(byClass(body,"news-provider-total")[0].textContent,/127|١٢٧/);
  ui.I18n.setLanguage("en");
  assert.equal(body.dir,"ltr");assert.equal(body.lang,"en");
  assert.equal(byClass(body,"news-headline")[0].textContent,"Original English headline");
  assert.equal(byClass(body,"news-summary")[0].textContent,"Original English description.");
  assert.equal(byClass(body,"news-count")[0].textContent,"Page 1 · 1 articles shown");
  await ui.elements.get("copyBtn").click();assert.equal(ui.copied(),data.response_en);
  ui.I18n.setLanguage("ar");
  await ui.elements.get("copyBtn").click();assert.equal(ui.copied(),data.response_ar);
  assert.equal(ui.requests.length,0,"Language switching and copying must not refetch or translate.");
  assert.equal(ui.getCurrentReport().id,data.report_id);
});

test("NewsData Arabic translation failures retain English and unsafe links never become clickable", () => {
  const ui=harness();const data=newsdataReport();
  data.items[0]={...data.items[0],translation_status:"original_only",headline_ar:null,summary_ar:null};
  data.items.push({...data.items[0],url:"https://127.0.0.1/private"});
  data.item_count=2;ui.displayResult(data);const body=ui.elements.get("responseBody");
  assert.equal(byClass(body,"news-headline")[0].textContent,"Original English headline");
  assert.equal(byClass(body,"news-item")[0].lang,"en");
  assert.match(byClass(body,"news-translation-note")[0].textContent,/الترجمة العربية غير متاحة/);
  assert.equal(byClass(body,"news-source-link").length,1);
  assert.equal(byClass(body,"news-link-unavailable").length,1);
  assert.equal(ui.requests.length,0);
});

test("NewsData keeps only the first 50 results even for a legacy larger provider page", () => {
  const ui=harness(null,{language:"en"}),data=newsdataReport({freshness:{plan:"paid"}});
  data.items=Array.from({length:55},(_,i)=>({...data.items[0],article_id:"fixture-"+i,headline_en:"Article "+i}));
  data.item_count=55;data.pagination.page_size=55;ui.displayResult(data);
  assert.equal(byClass(ui.elements.get("responseBody"),"news-item").length,50);
  assert.match(byClass(ui.elements.get("responseBody"),"news-count")[0].textContent,/50 articles/);
  assert.equal(byClass(ui.elements.get("responseBody"),"news-page-next")[0].disabled,true);
  assert.match(ui.elements.get("responseBody").textContent,/first 50 results/);
  assert.doesNotMatch(ui.elements.get("responseBody").textContent,/12-hour|of 6/);
});

test("the result limit blocks stale Next links and stays active after changing language", async () => {
  const ui=harness(null,{language:"en"});ui.setAuthenticated();ui.chooseAgent("iran-news");
  const data=newsdataReport({pagination:{has_more:true,page_index:5,page_size:10,results_before_page:40,
    results_consumed:50,result_limit:50,limit_reached:true,parent_report_id:"rpt_parent",next_report_id:"rpt_stale_third"}});
  ui.displayResult(data);let body=ui.elements.get("responseBody");
  assert.equal(byClass(body,"news-page-next")[0].disabled,true);
  await byClass(body,"news-page-next")[0].click();
  assert.equal(ui.requests.length,0);
  ui.I18n.setLanguage("ar");
  assert.equal(byClass(body,"news-page-next")[0].disabled,true);
  assert.equal(byClass(body,"news-page-previous")[0].disabled,false);
  assert.match(body.textContent,/50 نتيجة/);
});

test("Next and Previous show saved report pages and preserve the original request language", async () => {
  const first=newsdataReport({task:"Original scheduled task"});
  const second=newsdataReport({report_id:"rpt_newsdata_page_2",task:first.task,
    pagination:{has_more:false,page_index:2,page_size:10,parent_report_id:first.report_id,root_report_id:first.report_id},
    items:[{...first.items[0],headline_en:"Second page only"}]});
  const savedFirst={report_id:first.report_id,agent_id:"iran-news",status:"succeeded",language:"ar",task:first.task,
    result:first,pagination:{...first.pagination,next_report_id:second.report_id,next_report_status:"succeeded"}};
  const savedSecond={report_id:second.report_id,agent_id:"iran-news",status:"succeeded",language:"ar",task:first.task,result:second};
  const ui=harness(null,{language:"en",fetch:(url,options,respond)=>{
    if(url.endsWith("/run"))return respond(second);
    if(url==="/v1/reports/"+first.report_id)return respond(savedFirst);
    if(url==="/v1/reports/"+second.report_id)return respond(savedSecond);
    return dashboardFixture(url,respond);
  }});
  ui.setAuthenticated();ui.chooseAgent("iran-news");ui.displayResult(first);
  let body=ui.elements.get("responseBody");
  await byClass(body,"news-page-next")[0].click();
  const run=ui.requests.find(request=>request.url.endsWith("/run"));
  assert.deepEqual(JSON.parse(run.options.body),{task:first.task,language:"ar",continue_from_report_id:first.report_id});
  assert.equal(ui.getCurrentReport().id,second.report_id);
  assert.equal(byClass(body,"news-item").length,1);
  assert.equal(byClass(body,"news-headline")[0].textContent,"Second page only");
  assert.equal(byClass(body,"news-page-next")[0].disabled,true);
  await byClass(body,"news-page-previous")[0].click();
  assert.equal(ui.getCurrentReport().id,first.report_id);
  await byClass(body,"news-page-next")[0].click();
  assert.equal(ui.getCurrentReport().id,second.report_id);
  assert.equal(ui.requests.filter(request=>request.url.endsWith("/run")).length,1,"Saved page navigation must not charge a second time.");
});

test("an interrupted continuation keeps its idempotency key and the current page visible", async () => {
  const ui=harness(null,{fetch:(url,_options,respond)=>{
    if(url.endsWith("/run"))throw new Error("Synthetic interrupted response");
    return dashboardFixture(url,respond);
  }});
  ui.setAuthenticated();ui.chooseAgent("iran-news");const data=newsdataReport();ui.displayResult(data);
  const body=ui.elements.get("responseBody");
  await byClass(body,"news-page-next")[0].click();await byClass(body,"news-page-next")[0].click();
  const runs=ui.requests.filter(request=>request.url.endsWith("/run"));
  assert.equal(runs.length,2);
  assert.equal(runs[0].options.headers["Idempotency-Key"],runs[1].options.headers["Idempotency-Key"]);
  assert.equal(ui.getCurrentReport().id,data.report_id);
  assert.equal(byClass(body,"news-item").length,1);
});

test("NewsData download requests the visible language and a failed child is not retried", async () => {
  const ui=harness(null,{language:"en"});ui.setAuthenticated();
  const data=newsdataReport();data.pagination.next_report_id="rpt_failed_page";data.pagination.next_report_status="failed";
  ui.displayResult(data);await ui.elements.get("downloadBtn").click();
  assert.equal(ui.requests[0].url,"/v1/reports/"+data.report_id+"/download?format=md&language=en");
  assert.equal(byClass(ui.elements.get("responseBody"),"news-page-next")[0].disabled,true);
  assert.match(ui.elements.get("responseBody").textContent,/next-page attempt could not complete/);
});

test("Iran’s manual query is fixed to iran war even if the form value is changed", async () => {
  const ui=harness(null,{fetch:(url,options,respond)=>{
    if(url.endsWith("/run"))return respond(newsdataReport());
    return dashboardFixture(url,respond);
  }});
  ui.setAuthenticated();ui.chooseAgent("iran-news");
  assert.equal(ui.elements.get("task").readOnly,true);assert.equal(ui.elements.get("task").value,"iran war");
  ui.elements.get("task").value="unrelated topic";await ui.runTask();
  const run=ui.requests.find(request=>request.url.endsWith("/run"));assert.equal(JSON.parse(run.options.body).task,"iran war");
  assert.equal(ui.elements.get("task").value,"iran war");
});
