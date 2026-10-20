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

test("English translates every static chrome label and all 13 assistants, with only language persisted", () => {
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
  assert.equal(fixtureAgents.length,13);
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
  assert.equal(ui.chatCalls.language,2);
});

test("partial publisher research and separate event dates are visible and localized", () => {
  const ui=harness(null,{language:"en"});
  ui.displayResult(report({language:"en",items:[item({headline:"A current article about an earlier event",summary:"Synthetic summary.",event_date:"2026-10-18",event_timing_relative_to_window:"before_window"})],web_search:{research_incomplete:true,failed_request_count:2}}));
  const body=ui.elements.get("responseBody");
  assert.match(byClass(body,"news-research-warning")[0].textContent,/did not complete.*\(2\)/);
  assert.match(byClass(body,"news-event-date")[0].textContent,/Event date reported in research notes: 18 October 2026/);
  assert.equal(byClass(body,"news-list").length,1);
  assert.equal(byClass(body,"news-background").length,0);
  assert.equal(body.dir,"ltr");
  ui.I18n.setLanguage("ar");
  assert.match(byClass(body,"news-research-warning")[0].textContent,/لم يكتمل البحث/);
  assert.equal(body.dir,"ltr");
});

test("scripts initialize in order without chat requests before authentication; logout clears private state", async () => {
  const sources=[...html.matchAll(/<script src="([^"]+)"/g)].map(match=>match[1]);
  assert.deepEqual(sources,["/static/i18n.js?v=1.4.1","/static/chat.js?v=1.4.1","/static/app.js?v=1.4.1"]);
  const ui=harness(null,{language:"en",fetch:(url,_options,respond)=>dashboardFixture(url,respond)});
  assert.equal(ui.chatCalls.create,1);assert.equal(ui.chatCalls.login,0);assert.equal(ui.requests.length,0);
  assert.equal(ui.chatConfig.getCurrentReport(),null);
  ui.elements.get("username").value="fixture-user";ui.elements.get("password").value="fixture-password";
  await ui.login();
  assert.equal(ui.chatCalls.login,1);
  assert.equal(ui.elements.get("password").value,"");
  assert.equal(ui.elements.get("workspaceTabs").classList.contains("hidden"),false);
  assert.equal(ui.elements.get("selectedName").textContent,"Global Geopolitical Briefing");
  assert.match(ui.elements.get("schedulerNotice").textContent,/scheduler is running/);
  ui.displayResult(report({language:"ar"}));ui.showWorkspace("chat");
  assert.equal(ui.chatConfig.getCurrentReport().id,"rpt_ui_fixture");
  assert.equal(ui.elements.get("chatView").classList.contains("hidden"),false);
  ui.elements.get("task").value="private draft";ui.elements.get("scheduleTask").value="private schedule";
  ui.logout();
  assert.equal(ui.chatCalls.logout,1);assert.equal(ui.chatConfig.getCurrentReport(),null);
  for(const id of ["responseBody","archiveList","scheduleList","selectedName","selectedDescription"])assert.equal(ui.elements.get(id).textContent,"");
  for(const id of ["username","password","task","scheduleTask"])assert.equal(ui.elements.get(id).value,"");
  assert.equal(ui.elements.get("chatView").classList.contains("hidden"),true);
  assert.equal(ui.elements.get("workspaceTabs").classList.contains("hidden"),true);
  assert.equal(ui.I18n.getLanguage(),"en");assert.deepEqual([...ui.storage],[['aihub.language','en']]);
  const count=ui.requests.length;
  await assert.rejects(ui.chatConfig.request("/v1/chat/conversations"),error=>error.status===401);
  assert.equal(ui.requests.length,count);
  assert.equal(ui.context.window.authHeader,undefined);
});

test("the chat request closure prevents cross-origin auth, ignores auth overrides and rejects stale session responses", async () => {
  let release;
  const ui=harness(null,{fetch:async(url,options,respond)=>{
    if(url==="/v1/chat/conversations/pending")return new Promise(resolve=>{release=()=>resolve(respond({messages:[{content:"private response"}]}));});
    return respond({conversations:[]});
  }});
  ui.setAuthenticated();
  await assert.rejects(ui.chatConfig.request("https://unapproved.test/v1/chat/conversations"),error=>error.status===400);
  assert.equal(ui.requests.length,0);
  await ui.chatConfig.request("/v1/chat/conversations","POST",{title:"Fixture"},{Authorization:"unsafe override","Idempotency-Key":"fixture-key"});
  assert.equal(ui.requests[0].options.headers.Authorization,"Basic fixture");
  assert.equal(ui.requests[0].options.headers["Idempotency-Key"],"fixture-key");
  assert.equal(ui.requests[0].url,"/v1/chat/conversations");
  const pending=ui.chatConfig.request("/v1/chat/conversations/pending");
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
