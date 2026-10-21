// Run with: node tests/test_ui_public.js
// These checks exercise the shipped renderer with a minimal DOM, without a browser or network.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");
const vm = require("node:vm");

const staticPath = path.join(__dirname, "../app/static");
const html = fs.readFileSync(path.join(staticPath, "public.html"), "utf8");
const script = fs.readFileSync(path.join(staticPath, "public.js"), "utf8");

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

function harness(options={}){
  const elements=new Map(),tree=new Element("document"),stack=[tree],voidTags=new Set(["META","LINK","INPUT","BR","HR","IMG"]);
  for(const match of html.matchAll(/<\/?([a-zA-Z][a-zA-Z0-9-]*)\b([^>]*)>|([^<]+)/g)){
    if(match[3]!==undefined){stack.at(-1).append(match[3]);continue;}
    if(match[0].startsWith("</")){if(stack.length>1)stack.pop();continue;}
    const element=new Element(match[1]);
    for(const attribute of match[2].matchAll(/([a-zA-Z_][\w-]*)(?:="([^"]*)")?/g))element.setAttribute(attribute[1],attribute[2]??"");
    if(element.id)elements.set(element.id,element);stack.at(-1).append(element);if(!voidTags.has(element.tagName))stack.push(element);
  }
  const body=descendants(tree,el=>el.tagName==="BODY")[0],requests=[],listeners=new Map(),storage=new Map();
  if(options.language)storage.set("aihub.language",options.language);
  const document={body,documentElement:descendants(tree,el=>el.tagName==="HTML")[0],title:"",getElementById:id=>elements.get(id),createElement:tag=>new Element(tag),createTextNode:text=>({nodeType:3,textContent:String(text)}),querySelectorAll:selector=>tree.querySelectorAll(selector)};
  let chatConfig,copied="",uuid=0;
  const respond=(data,status=200)=>({ok:status>=200&&status<300,status,text:async()=>JSON.stringify(data)});
  const context={document,URL,Blob,crypto:{randomUUID:()=>"public-fixture-"+(++uuid)},setTimeout:()=>0,
    navigator:{clipboard:{writeText:async text=>{copied=text;}}},CustomEvent:class{constructor(type,init={}){this.type=type;this.detail=init.detail;}},
    window:{location:{origin:"https://ai-hub.test"},localStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,String(value)),removeItem:key=>storage.delete(key)},
      addEventListener:(type,handler)=>{if(!listeners.has(type))listeners.set(type,[]);listeners.get(type).push(handler);},dispatchEvent:event=>{for(const handler of listeners.get(event.type)||[])handler(event);},
      AIHubChat:{create:config=>{chatConfig=config;return{login:async()=>{},setLanguage(){},refreshReportContext(){}};}}},
    fetch:async(url,requestOptions)=>{requests.push({url,options:requestOptions});
      if(url==="/v1/public/bootstrap")return respond({agents:[{id:"iran-news",name:"أخبار حرب إيران",name_en:"Iran war news",enabled:true},{id:"decision-briefing",name:"موجز القرار",name_en:"Decision briefing",enabled:true}]});
      if(options.fetch)return options.fetch(url,requestOptions,respond);return respond(page());}
  };
  const instrumented=script.replace(/\n  start\(\);\n\}\)\(\);\s*$/, '\n  globalThis.publicUI={start,search,request,showResult,chooseAgent,changePage,currentReport};\n})();');
  assert.notEqual(instrumented,script);
  vm.createContext(context);vm.runInContext(fs.readFileSync(path.join(staticPath,"i18n.js"),"utf8"),context);vm.runInContext(instrumented,context);
  return{...context.publicUI,elements,body,requests,storage,I18n:context.window.AIHubI18n,copied:()=>copied,chatConfig:()=>chatConfig};
}
function page(overrides={}){return{report_id:"rpt_public_page1",agent_id:"iran-news",task:"iran war",language:"ar",provider:"newsdata",response:"تقرير محفوظ",response_ar:"العنوان العربي",response_en:"English headline",
  items:[{headline_ar:"العنوان العربي",summary_ar:"ملخص عربي",headline_en:"English headline",summary_en:"English summary",translation_status:"translated",publisher:"Example Publisher",url:"https://www.reuters.com/world/example",published_date:"2026-10-20T08:00:00Z"}],
  pagination:{page_index:1,page_size:10,has_more:true,results_before_page:0,results_consumed:10,result_limit:50,limit_reached:false},freshness:{plan:"paid"},...overrides};}
async function key(ui,details){let prevented=false;for(const listener of ui.elements.get("publicTask").events.keydown||[])await listener({key:"Enter",shiftKey:false,isComposing:false,preventDefault(){prevented=true;},...details});return prevented;}

test("the public entry has no login or cost panels and uses a separate public script",()=>{
  assert.doesNotMatch(html,/type="password"|id="username"|id="password"|dailyCost|monthlyCost|accountOverview|usage-details|loginBox/);
  assert.match(html,/public\.js\?v=1\.5\.1/);assert.doesNotMatch(html,/src="\/static\/app\.js/);
});
test("Enter searches directly after anonymous bootstrap with no Authorization header",async()=>{
  const ui=harness();await ui.start();assert.equal(ui.elements.get("publicSearchButton").disabled,false);
  assert.equal(ui.chatConfig().endpoint,"/v1/public/chat/conversations");
  assert.equal(await key(ui),true);const run=ui.requests.find(x=>x.url.endsWith("/run"));
  assert.deepEqual(JSON.parse(run.options.body),{task:"iran war",language:"ar"});
  assert.equal(run.options.credentials,"same-origin");assert.equal(run.options.headers.Authorization,undefined);
  assert.equal(ui.currentReport().id,"rpt_public_page1");
  assert.equal(byClass(ui.elements.get("publicResultBody"),"news-headline")[0].textContent,"العنوان العربي");
});
test("Shift Enter and composition Enter keep the draft without submitting",async()=>{
  const ui=harness();await ui.start();ui.chooseAgent("decision-briefing");ui.elements.get("publicTask").value="A draft";
  assert.equal(await key(ui,{shiftKey:true}),false);assert.equal(await key(ui,{isComposing:true}),false);
  assert.equal(ui.requests.filter(x=>x.url.endsWith("/run")).length,0);assert.equal(ui.elements.get("publicTask").value,"A draft");
});
test("repeated Enter while a search is running cannot submit a second request",async()=>{
  let resolve;const ui=harness({fetch:(_url,_options,respond)=>new Promise(done=>{resolve=()=>done(respond(page()));})});await ui.start();
  const pending=key(ui);await Promise.resolve();await key(ui);assert.equal(ui.requests.filter(x=>x.url.endsWith("/run")).length,1);resolve();await pending;
});
test("language switching changes saved news and copy text without another provider request",async()=>{
  const ui=harness();await ui.start();await key(ui);const count=ui.requests.length;
  ui.I18n.setLanguage("en");assert.equal(byClass(ui.elements.get("publicResultBody"),"news-headline")[0].textContent,"English headline");
  await ui.elements.get("publicCopy").click();assert.equal(ui.copied(),"English headline");
  assert.equal(ui.requests.length,count);assert.doesNotMatch(ui.body.textContent,/\{[a-zA-Z]+\}|public\.[a-zA-Z]+/);
});
test("the fiftieth provider result stops Next even if a stale saved child is supplied",async()=>{
  const ui=harness();await ui.start();ui.showResult(page({pagination:{page_index:5,page_size:10,has_more:true,results_before_page:40,results_consumed:50,limit_reached:true,parent_report_id:"rpt_first",next_report_id:"rpt_third"}}));
  const body=ui.elements.get("publicResultBody");assert.equal(byClass(body,"news-page-next")[0].disabled,true);
  const count=ui.requests.length;await byClass(body,"news-page-next")[0].click();assert.equal(ui.requests.length,count);
  assert.equal(byClass(body,"news-page-previous")[0].disabled,false);assert.match(body.textContent,/50 نتيجة/);
});
test("public requests cannot target protected admin routes or forward supplied credentials",async()=>{
  const ui=harness();await ui.start();
  await assert.rejects(ui.request("/v1/usage"),error=>error.status===400);
  await assert.rejects(ui.request("https://external.test/v1/public/bootstrap"),error=>error.status===400);
  await ui.request("/v1/public/bootstrap","GET",undefined,{Authorization:"private-value"});
  assert.equal(ui.requests.at(-1).options.headers.Authorization,undefined);
});
test("unconfirmed search replay retains its idempotency key and hides raw error details",async()=>{
  const ui=harness({fetch:()=>{throw new Error("PRIVATE ERROR");}});await ui.start();await key(ui);await key(ui);
  const runs=ui.requests.filter(x=>x.url.endsWith("/run"));assert.equal(runs.length,2);assert.equal(runs[0].options.headers["Idempotency-Key"],runs[1].options.headers["Idempotency-Key"]);
  assert.doesNotMatch(ui.body.textContent,/PRIVATE ERROR/);assert.match(ui.elements.get("publicNotice").textContent,/المحاولة نفسها/);
});
test("public chat errors retain a valid turn ID for recovery without exposing private diagnostics",async()=>{
  const turn="cht_"+"a".repeat(32);
  const ui=harness({fetch:(_url,_options,respond)=>respond({detail:{code:"chat_in_progress",turn_id:turn,message:"PRIVATE ERROR",cost_usd:99,conversation_id:"invalid"}},409)});
  await ui.start();
  await assert.rejects(ui.request("/v1/public/chat/conversations/example/messages","POST",{}),error=>{
    assert.equal(error.detail.turn_id,turn);assert.equal(error.code,"chat_in_progress");
    assert.equal(error.detail.cost_usd,undefined);assert.equal(error.detail.message,undefined);assert.equal(error.detail.conversation_id,undefined);
    assert.doesNotMatch(error.message,/PRIVATE ERROR|99/);return true;
  });
});
test("unsafe article links stay unclickable and Arabic translation fallback remains readable",async()=>{
  const ui=harness();await ui.start();const data=page();data.items[0]={...data.items[0],headline_ar:null,summary_ar:null,translation_status:"original_only",url:"https://127.0.0.1/private"};ui.showResult(data);
  const body=ui.elements.get("publicResultBody");assert.equal(byClass(body,"news-source-link").length,0);assert.equal(byClass(body,"news-headline")[0].textContent,"English headline");
  assert.match(body.textContent,/الترجمة العربية غير متاحة/);
});

