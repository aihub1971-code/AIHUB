(function(){
  "use strict";
  const I18n = window.AIHubI18n;
  if(!I18n) return;
  const $ = id => document.getElementById(id);
  const t = (key, params) => I18n.t(key, params);
  const n = value => I18n.number(value);
  let authHeader = "", agents = [], selectedAgent = null, lastResponse = "", latestReportId = null;
  let lastResult = null, editingScheduleId = null, archiveOffset = 0, lastAttempt = null;
  let runInFlight = false, loginBusy = false, scheduleBusy = false, copied = false, sessionVersion = 0;
  let dashboardCache = null, healthState = null, runHintKey = "task.runHint", activeWorkspace = "reports";
  let chat = null, scheduleLanguageExplicit = false, newsPageAttempt = null;
  const activeRequests = new Set(), notices = new Map();
  function noticeParams(params){
    const output={...params};
    if(output.message && typeof output.message === "object" && output.message.i18nKey)
      output.message=t(output.message.i18nKey,output.message.i18nParams);
    return output;
  }
  function setNotice(id, key, kind="", params={}){
    notices.set(id,{key,kind,params});
    const el=$(id);
    el.textContent=t(key,noticeParams(params));
    el.className="notice"+(kind ? " "+kind : "");
  }
  function hideNotice(id){ notices.delete(id); $(id).textContent=""; $(id).className="notice hidden"; }
  function setError(id,error){
    if(error?.code==="session_ended") return;
    setNotice(id,error?.i18nKey || "error.network","error",error?.i18nParams || {});
  }
  function makeError(data,status){
    const detail=data?.detail;
    const code=detail && typeof detail==="object" && typeof detail.code==="string" ? detail.code : "";
    let key=I18n.has("error."+code) ? "error."+code : status===401 ? "error.authentication_required" :
      status===403 ? "error.forbidden" : status===404 ? "error.not_found" :
      (status===400 || status===422) ? "error.invalid_request" : "error.generic";
    const error=new Error(t(key,{status:status || "—"}));
    error.code=code || (status===401 ? "authentication_required" : "");
    error.status=status; error.detail=detail && typeof detail==="object" ? detail : {};
    error.i18nKey=key; error.i18nParams={status:status || "—"};
    return error;
  }
  function sessionError(){ return makeError({detail:{code:"session_ended"}},401); }
  function prettyError(data,status){ return makeError(data,status).message; }
  async function readJson(response){
    try { const text=await response.text(); return text ? JSON.parse(text) : {}; } catch(_) { return {}; }
  }
  async function authenticatedRequest(path,method="GET",body,extraHeaders={},blob=false){
    if(!authHeader) throw makeError({},401);
    const origin=window.location.origin;
    let target;
    try{ target=new URL(path,origin); }catch(_){ throw makeError({},400); }
    if(typeof path!=="string" || !path.startsWith("/v1/") || /[\u0000-\u0020\u007f\\]/.test(path) ||
       target.origin!==origin || !target.pathname.startsWith("/v1/")) throw makeError({},400);
    const version=sessionVersion, authorization=authHeader;
    const controller=new AbortController(); activeRequests.add(controller);
    const headers={Authorization:authorization};
    if(body!==undefined) headers["Content-Type"]="application/json";
    if(typeof extraHeaders["Idempotency-Key"]==="string") headers["Idempotency-Key"]=extraHeaders["Idempotency-Key"];
    try{
      const response=await fetch(target.pathname+target.search,{method,headers,cache:"no-store",signal:controller.signal,
        body:body===undefined ? undefined : JSON.stringify(body)});
      const data=blob && response.ok ? await response.blob() : await readJson(response);
      if(version!==sessionVersion || !authHeader) throw sessionError();
      if(!response.ok){
        const error=makeError(data,response.status);
        if(response.status===401) logout();
        throw error;
      }
      return data;
    }catch(error){
      if(version!==sessionVersion || !authHeader) throw sessionError();
      throw error;
    }finally{ activeRequests.delete(controller); }
  }
  const apiJson=(path,method="GET",body,extraHeaders={})=>authenticatedRequest(path,method,body,extraHeaders);
  const money=value=>Number(value || 0).toLocaleString(I18n.getLocale(),{style:"currency",currency:"USD",minimumFractionDigits:2,maximumFractionDigits:4});
  const localDate=value=>{const date=new Date(value);return Number.isNaN(date.getTime()) ? "—" : date.toLocaleString(I18n.getLocale(),{timeZone:"Asia/Dubai",dateStyle:"short",timeStyle:"short"});};
  function agentName(id){
    if(I18n.has("agent."+id+".name")) return t("agent."+id+".name");
    return agents.find(agent=>agent.id===id)?.name || id || t("news.title");
  }
  function agentDescription(agent){
    return I18n.has("agent."+agent.id+".description") ? t("agent."+agent.id+".description") :
      I18n.getLanguage()==="en" ? t("agents.noDescription") : agent.description || t("agents.noDescription");
  }
  function appendInline(parent, source){
    const text = String(source || "");
    const pattern = /\[([^\]]+)\]\(<?(https?:\/\/[^)\s>]+)>?\)|\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|(https?:\/\/[^\s<>]+)/g;
    let cursor = 0, match;
    while((match = pattern.exec(text)) !== null){
      if(match.index > cursor) parent.append(document.createTextNode(text.slice(cursor, match.index)));
      if(match[1] !== undefined){
        const a = document.createElement("a");
        a.textContent = match[1]; a.href = match[2]; a.target = "_blank"; a.rel = "noopener noreferrer";
        parent.append(a);
      }else if(match[3] !== undefined){
        const el = document.createElement("strong"); el.textContent = match[3]; parent.append(el);
      }else if(match[4] !== undefined){
        const el = document.createElement("em"); el.textContent = match[4]; parent.append(el);
      }else if(match[5] !== undefined){
        const el = document.createElement("code"); el.textContent = match[5]; parent.append(el);
      }else if(match[6] !== undefined){
        let url = match[6], suffix = "";
        while(/[.,;:!?]$/.test(url)){ suffix = url.slice(-1) + suffix; url = url.slice(0,-1); }
        const a = document.createElement("a"); a.textContent = url; a.href = url; a.target = "_blank"; a.rel = "noopener noreferrer";
        parent.append(a);
        if(suffix) parent.append(document.createTextNode(suffix));
      }
      cursor = pattern.lastIndex;
    }
    if(cursor < text.length) parent.append(document.createTextNode(text.slice(cursor)));
  }
  function renderMarkdown(target, source){
    target.replaceChildren();
    const lines = String(source || "").replace(/\r/g, "").split("\n");
    let i = 0, paragraph = [], list = null, listKind = "";
    const flushParagraph = () => {
      if(!paragraph.length) return;
      const p = document.createElement("p");
      appendInline(p, paragraph.join(" "));
      target.append(p); paragraph = [];
    };
    const flushList = () => { list = null; listKind = ""; };
    const cells = line => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(x => x.trim());
    const isTableDivider = line => /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
    while(i < lines.length){
      const line = lines[i], trimmed = line.trim();
      if(!trimmed){ flushParagraph(); flushList(); i++; continue; }
      if(trimmed.startsWith("```")){
        flushParagraph(); flushList();
        i++; const code = [];
        while(i < lines.length && !lines[i].trim().startsWith("```")) code.push(lines[i++]);
        if(i < lines.length) i++;
        const pre = document.createElement("pre"); pre.textContent = code.join("\n"); target.append(pre); continue;
      }
      if(i + 1 < lines.length && trimmed.includes("|") && isTableDivider(lines[i+1])){
        flushParagraph(); flushList();
        const headers = cells(line), tableWrap = document.createElement("div");
        tableWrap.className = "response-table-wrap";
        const table = document.createElement("table"), thead = document.createElement("thead"), headRow = document.createElement("tr");
        headers.forEach(value => { const th = document.createElement("th"); appendInline(th,value); headRow.append(th); });
        thead.append(headRow); table.append(thead);
        i += 2;
        const tbody = document.createElement("tbody");
        while(i < lines.length && lines[i].trim().includes("|") && lines[i].trim() !== ""){
          const values = cells(lines[i++]), tr = document.createElement("tr");
          for(let col=0; col<headers.length; col++){
            const td = document.createElement("td"); appendInline(td, values[col] || "—"); tr.append(td);
          }
          tbody.append(tr);
        }
        table.append(tbody); tableWrap.append(table); target.append(tableWrap); continue;
      }
      const heading = trimmed.match(/^(#{1,4})\s+(.+)$/);
      if(heading){
        flushParagraph(); flushList();
        const el = document.createElement("h"+Math.min(heading[1].length+1,4)); appendInline(el,heading[2]); target.append(el); i++; continue;
      }
      if(/^(-{3,}|\*{3,}|_{3,})$/.test(trimmed)){
        flushParagraph(); flushList(); target.append(document.createElement("hr")); i++; continue;
      }
      const quote = trimmed.match(/^>\s?(.*)$/);
      if(quote){
        flushParagraph(); flushList();
        const block = document.createElement("blockquote"); appendInline(block,quote[1]); target.append(block); i++; continue;
      }
      const bullet = trimmed.match(/^[-*+]\s+(.+)$/);
      const numbered = trimmed.match(/^(\d+)[.)]\s+(.+)$/);
      if(bullet || numbered){
        flushParagraph();
        const kind = bullet ? "ul" : "ol";
        if(!list || listKind !== kind){ flushList(); list = document.createElement(kind); listKind = kind; target.append(list); }
        const li = document.createElement("li");
        if(numbered) li.value = Number(numbered[1]);
        appendInline(li,bullet ? bullet[1] : numbered[2]); list.append(li); i++; continue;
      }
      flushList(); paragraph.push(trimmed); i++;
    }
    flushParagraph();
  }
  const newsPublishers=[
    ["ft.com","Financial Times"],["theatlantic.com","The Atlantic"],["politico.com","Politico"],
    ["politico.eu","Politico"],["wsj.com","The Wall Street Journal"],["telegraph.co.uk","The Daily Telegraph"],
    ["aljazeera.com",null],["aljazeera.net",null]
  ];
  function officialNewsSource(value){
    if(typeof value!=="string" || /[\u0000-\u0020\u007f\\]/.test(value)) return null;
    try{
      const url=new URL(value);
      if(!["https:","http:"].includes(url.protocol) || url.username || url.password || url.port) return null;
      const hostname=url.hostname.toLowerCase().replace(/\.$/,"");
      const publisher=newsPublishers.find(([domain])=>hostname===domain || hostname.endsWith("."+domain));
      if(!publisher || !url.pathname || url.pathname==="/") return null;
      return {url:url.href,publisher:publisher[1] || t("news.aljazeera")};
    }catch(_){return null;}
  }
  function providerNewsSource(item){
    const value=item.url;
    if(typeof value!=="string" || /[\u0000-\u0020\u007f\\]/.test(value))return null;
    try{
      const url=new URL(value),hostname=url.hostname.toLowerCase().replace(/\.$/,"");
      if(!["https:","http:"].includes(url.protocol) || url.username || url.password || url.port ||
        !url.pathname || url.pathname==="/" || !hostname.includes(".") ||
        !/^[a-z0-9.-]+$/.test(hostname) || /^[\d.]+$/.test(hostname) ||
        /(?:^|\.)(?:localhost|local|internal|lan|home|invalid)$/.test(hostname))return null;
      return {url:url.href,publisher:typeof item.publisher==="string" && item.publisher.trim() ? item.publisher.slice(0,160) : hostname};
    }catch(_){return null;}
  }
  const isNewsDataReport=data=>data?.provider==="newsdata" && data.agent_id==="iran-news";
  const NEWSDATA_RESULT_LIMIT=20;
  function newsDataResultsBefore(data){
    const p=data?.pagination || {};
    if(Number.isSafeInteger(p.results_before_page) && p.results_before_page>=0)return p.results_before_page;
    return Math.max(0,(Number(p.page_index) || 1)-1)*(Number(p.page_size) || 10);
  }
  function newsDataLimitReached(data){
    const p=data?.pagination || {};
    if(p.limit_reached===true)return true;
    if(Number.isSafeInteger(p.results_consumed))return p.results_consumed>=NEWSDATA_RESULT_LIMIT;
    const count=data?.newsdata?.returned_item_count ?? data?.items?.length ?? 0;
    return newsDataResultsBefore(data)+count>=NEWSDATA_RESULT_LIMIT;
  }
  function newsElement(tag,className,text){
    const element=document.createElement(tag); if(className)element.className=className;
    if(text!==undefined)element.textContent=String(text);
    return element;
  }
  function newsDay(value){
    if(typeof value!=="string" || !/^\d{4}-\d{2}-\d{2}(?:$|T)/.test(value)) return "";
    const date=new Date(value.length===10 ? value+"T12:00:00+04:00" : value);
    return Number.isNaN(date.getTime()) ? "" : date.toLocaleDateString(I18n.getLocale(),{timeZone:"Asia/Dubai",year:"numeric",month:"long",day:"numeric"});
  }
  function renderNewsItem(item,background,provider=false){
    const article=newsElement("article","news-item");
    const headline=typeof item.headline==="string" ? item.headline : t("news.fallbackHeadline");
    const heading=newsElement("h3","news-headline",headline); heading.dir="auto";
    const summary=newsElement("p","news-summary",typeof item.summary==="string" && item.summary.trim() ? item.summary : t("news.noSummary")); summary.dir="auto";
    article.append(heading,summary);
    const source=provider ? providerNewsSource(item) : officialNewsSource(item.url), meta=newsElement("div","news-byline"); meta.dir=I18n.getDirection();
    if(source)meta.append(newsElement("bdi","news-publisher",source.publisher));
    if(item.source_kind==="live_coverage")meta.append(newsElement("span","news-context-label news-live-coverage",t("news.liveCoverage")));
    const published=provider ? item.published_date : item.published_day_dubai || item.published_date;
    const day=provider && Number.isFinite(Date.parse(published)) ? localDate(published) : newsDay(published);
    if(day){const time=newsElement("time","news-published",t("news.published",{date:day}));time.dateTime=published;meta.append(time);}
    else meta.append(newsElement("span","news-published",t("news.noDate")));
    const eventDay=newsDay(item.event_date);
    if(eventDay)meta.append(newsElement("span","news-event-date",t("news.eventDate",{date:eventDay})));
    if(background)meta.append(newsElement("span","news-context-label",t(I18n.has("news.context."+item.classification) ? "news.context."+item.classification : "news.background")));
    article.append(meta);
    if(source){
      const link=newsElement("a","news-source-link",t("news.openSource"));link.dir=I18n.getDirection();
      link.href=source.url;link.target="_blank";link.rel="noopener noreferrer";
      link.setAttribute("aria-label",t("news.sourceAria",{publisher:source.publisher,headline}));
      const icon=newsElement("span","","↗");icon.setAttribute("aria-hidden","true");link.append(icon);article.append(link);
    }else{const unavailable=newsElement("p","news-link-unavailable",t("news.noLink"));unavailable.dir=I18n.getDirection();article.append(unavailable);}
    return article;
  }
  function renderNewsDataReport(target,data){
    const items=data.items.filter(item=>item && typeof item==="object" && !Array.isArray(item)).slice(0,Math.max(0,NEWSDATA_RESULT_LIMIT-newsDataResultsBefore(data)));
    const language=I18n.getLanguage(),pagination=data.pagination || {};
    target.replaceChildren();target.classList.add("news-reading");target.dir=I18n.getDirection();target.lang=language;
    const intro=newsElement("header","news-report-intro");
    intro.append(newsElement("div","news-provider-badge","NewsData · iran war"),
      newsElement("h2","news-report-title",t("agent.iran-news.name")),
      newsElement("p","news-coverage",t("newsdata.window")));
    if(data.generated_at && Number.isFinite(Date.parse(data.generated_at)))intro.append(newsElement("p","news-prepared",t("news.prepared",{date:localDate(data.generated_at)})));
    intro.append(newsElement("p","news-count",t("newsdata.pageCount",{page:n(pagination.page_index || 1),count:n(items.length)})));
    intro.append(newsElement("p","news-result-limit",t("newsdata.limitNote")));
    const total=data.newsdata?.provider_total_results ?? pagination.total_results;
    if(Number.isSafeInteger(total) && total>=0)intro.append(newsElement("p","news-provider-total",t("newsdata.total",{count:n(total)})));
    target.append(intro);
    if(data.freshness?.plan==="free")target.append(newsElement("p","news-coverage-note",t("newsdata.freeDelay")));
    else if(data.freshness?.plan!=="paid")target.append(newsElement("p","news-section-note",t("newsdata.planUnknown")));
    const originalOnly=items.filter(item=>item.translation_status!=="translated").length;
    if(language==="ar" && originalOnly)target.append(newsElement("p","news-coverage-note news-translation-note",t("newsdata.translationPartial",{count:n(originalOnly)})));
    if(items.length){
      const list=newsElement("div","news-list");
      for(const item of items){
        const translated=language==="ar" && item.translation_status==="translated" && typeof item.headline_ar==="string" && item.headline_ar.trim();
        const headline=translated ? item.headline_ar : item.headline_en || item.headline;
        const summary=translated ? item.summary_ar : item.summary_en || "";
        const article=renderNewsItem({...item,headline,summary},false,true);
        article.lang=translated ? "ar" : "en";article.dir=translated ? "rtl" : "ltr";
        if(language==="ar"){
          const label=newsElement("p","news-translation-label",t(translated ? "newsdata.translated" : "newsdata.originalFallback"));label.dir=I18n.getDirection();label.lang=language;article.append(label);
        }
        list.append(article);
      }
      target.append(list);
    }else target.append(newsElement("p","news-empty",t("newsdata.empty")));
    const navigation=newsElement("nav","news-pagination");navigation.setAttribute("aria-label",t("newsdata.navigation"));
    const previous=newsElement("button","secondary news-page-button news-page-previous",t("newsdata.previous"));previous.type="button";
    previous.disabled=runInFlight || !pagination.parent_report_id;previous.addEventListener("click",()=>changeNewsPage("previous"));
    const next=newsElement("button","primary news-page-button news-page-next",t("newsdata.next"));next.type="button";
    next.disabled=runInFlight || newsDataLimitReached(data) || (!pagination.has_more && !pagination.next_report_id) || pagination.next_report_status==="failed";
    next.addEventListener("click",()=>changeNewsPage("next"));
    navigation.append(previous,newsElement("span","news-page-index",t("newsdata.page",{page:n(pagination.page_index || 1)})),next);target.append(navigation);
    target.append(newsElement("p","news-section-note",t(newsDataLimitReached(data) ? "newsdata.limitReached" : pagination.next_report_status==="failed" ? "newsdata.failedPage" : "newsdata.pageNote")));
    const notes=newsElement("details","news-notes");
    notes.append(newsElement("summary","",t("news.notes")),newsElement("p","",t("newsdata.provenance")));target.append(notes);
  }
  function renderPublisherCoverage(coverage){
    const families=[...new Set(newsPublishers.map(([,publisher])=>publisher || "Al Jazeera"))];
    if(!Array.isArray(coverage) || coverage.length!==families.length) return null;
    const count=value=>Number.isSafeInteger(value) && value>=0 && value<=20;
    if(coverage.some(row=>!row || typeof row!=="object" || Array.isArray(row)
      || !families.includes(row.publisher) || !count(row.item_count) || !count(row.background_count))
      || new Set(coverage.map(row=>row.publisher)).size!==families.length
      || coverage.reduce((total,row)=>total+row.item_count+row.background_count,0)>20) return null;
    const details=newsElement("details","news-source-coverage");
    details.dir=I18n.getDirection();details.lang=I18n.getLanguage();
    details.append(newsElement("summary","",t("news.publisherCoverage")),
      newsElement("p","news-source-coverage-intro",t("news.publisherCoverageNote")));
    const list=newsElement("ul","news-publisher-list");
    for(const publisher of families){
      const entry=coverage.find(row=>row.publisher===publisher),row=newsElement("li","news-publisher-row");
      const heading=newsElement("div","news-publisher-row-heading");
      const name=newsElement("bdi","news-publisher-name",publisher==="Al Jazeera" ? t("news.aljazeera") : publisher);
      const counts=newsElement("div","news-publisher-counts");
      counts.append(newsElement("span","news-publisher-item-count",t("news.publisherItems",{count:n(entry.item_count)})),
        newsElement("span","news-publisher-background-count",t("news.publisherBackground",{count:n(entry.background_count)})));
      heading.append(name,counts);row.append(heading);
      if(entry.item_count===0 && entry.background_count===0)
        row.append(newsElement("p","news-publisher-missing",t("news.publisherNotIncluded")));
      list.append(row);
    }
    details.append(list);return details;
  }
  function renderNewsReport(target,data){
    if(isNewsDataReport(data)){renderNewsDataReport(target,data);return;}
    const valid=item=>item && typeof item==="object" && !Array.isArray(item);
    const items=data.items.filter(valid).slice(0,20), background=data.background_items.filter(valid).slice(0,20-items.length);
    target.replaceChildren();target.classList.add("news-reading");
    target.dir=data.language==="en" ? "ltr" : "rtl";target.lang=data.language==="en" ? "en" : "ar";
    const intro=newsElement("header","news-report-intro");intro.dir=I18n.getDirection();intro.lang=I18n.getLanguage();
    intro.append(newsElement("h2","news-report-title",agentName(data.agent_id || selectedAgent?.id)));
    const start=newsDay(data.coverage_window?.start),end=newsDay(data.coverage_window?.end);
    if(start && end)intro.append(newsElement("p","news-coverage",t("news.coverage",{range:start===end ? start : start+" – "+end})));
    if(data.generated_at && Number.isFinite(Date.parse(data.generated_at)))intro.append(newsElement("p","news-prepared",t("news.prepared",{date:localDate(data.generated_at)})));
    const requested=Number.isInteger(data.target_count) && data.target_count>0 ? data.target_count : null;
    intro.append(newsElement("p","news-count",t(requested===null ? "news.count" : "news.requestedCount",{count:n(items.length),target:n(requested)})));target.append(intro);
    const publisherCoverage=renderPublisherCoverage(data.publisher_coverage);
    if(publisherCoverage)target.append(publisherCoverage);
    if(data.web_search?.research_incomplete){
      const warning=newsElement("p","news-coverage-note news-research-warning",t("news.researchIncomplete",{count:Number.isInteger(data.web_search.failed_request_count) ? n(data.web_search.failed_request_count) : "—"}));warning.dir=I18n.getDirection();target.append(warning);
    }
    if(requested!==null && items.length<requested){
      const note=newsElement("p","news-coverage-note",t("news.shortfall",{count:n(requested-items.length)})+" "+t(requested>20 ? "news.ceiling" : "news.evidenceOnly"));note.dir=I18n.getDirection();target.append(note);
    }
    if(items.length){const list=newsElement("div","news-list");items.forEach(item=>list.append(renderNewsItem(item,false)));target.append(list);}
    else{
      const empty=newsElement("div","news-empty");empty.dir=I18n.getDirection();
      empty.append(newsElement("p","",t("news.empty")),newsElement("p","",t(background.length ? "news.emptyBackground" : "news.emptyNote")));target.append(empty);
    }
    if(background.length){
      const section=newsElement("section","news-background");section.setAttribute("aria-label",t("news.background"));
      const title=newsElement("h2","news-section-title",t("news.background"));title.dir=I18n.getDirection();
      const note=newsElement("p","news-section-note",t("news.backgroundNote"));note.dir=I18n.getDirection();section.append(title,note);
      background.forEach(item=>section.append(renderNewsItem(item,true)));target.append(section);
    }
    const notes=newsElement("details","news-notes");notes.dir=I18n.getDirection();notes.lang=I18n.getLanguage();
    notes.append(newsElement("summary","",t("news.notes")),newsElement("p","",t("news.sourceNote")),newsElement("p","",t("news.dateNote")));
    if(Number.isInteger(data.fresh_event_count))notes.append(newsElement("p","",t("news.freshCount",{count:n(data.fresh_event_count)})));
    target.append(notes);
  }
  function renderHealth(){
    if(!healthState)return;
    $("healthDot").className="dot"+(healthState.ok ? " ok" : "");
    $("healthText").textContent=t(healthState.network ? "health.offline" : healthState.ok ? "health.connected" : "health.failed");
    $("runtimeLabel").textContent=t(healthState.network ? "health.modelUnknown" : healthState.ready ? "health.modelReady" : "health.modelPending");
  }
  async function fetchHealth(){
    try{const response=await fetch("/health",{cache:"no-store"}),data=await readJson(response);healthState={ok:response.ok,ready:data.stage==="ready"};}
    catch(_){healthState={network:true};}
    renderHealth();
  }
  function renderAgents(){
    const search=$("search").value.trim().toLowerCase();
    const shown=agents.filter(agent=>(agentName(agent.id)+" "+agent.id+" "+agentDescription(agent)).toLowerCase().includes(search));
    $("agentList").replaceChildren();$("listCount").textContent=n(shown.length)+" / "+n(agents.length);
    shown.forEach(agent=>{
      const button=newsElement("button","agent-item"+(selectedAgent?.id===agent.id ? " active" : ""));button.type="button";
      button.setAttribute("aria-pressed",String(selectedAgent?.id===agent.id));
      const detail=newsElement("span","");detail.append(newsElement("div","agent-name",agentName(agent.id)),newsElement("div","agent-id",agent.id));
      button.append(newsElement("span","agent-num",agent.number),detail);button.addEventListener("click",()=>chooseAgent(agent.id));$("agentList").append(button);
    });
    if(agents.length && !shown.length)$("agentList").append(newsElement("p","hint",t("agents.empty")));
    $("agentCount").textContent=n(agents.length);
  }
  function renderSelectedAgent(){
    if(!selectedAgent)return;
    $("selectedNum").textContent=selectedAgent.number;$("selectedName").textContent=agentName(selectedAgent.id);
    $("selectedId").textContent=selectedAgent.id;$("selectedDescription").textContent=agentDescription(selectedAgent);
    $("selectedStatus").textContent=t(selectedAgent.runtime_ready ? "agents.ready" : selectedAgent.id==="iran-news" ? "agents.newsdataPending" : "agents.registered");
    $("selectedStatus").className="pill"+(selectedAgent.runtime_ready ? "" : " warn");
    $("iranQueryNotice").classList.toggle("hidden",selectedAgent.id!=="iran-news");
    $("newsCoverageHint").classList.toggle("hidden",selectedAgent.id==="iran-news");
    $("skillTags").replaceChildren();
    (selectedAgent.skill_ids || []).forEach(id=>{
      const key=I18n.has("agent."+id+".name") ? "agent."+id+".name" : "skill."+id;
      $("skillTags").append(newsElement("span","tag",t("agents.skill",{name:I18n.has(key) ? t(key) : id})));
    });
  }
  function clearResult(){
    lastResponse="";lastResult=null;latestReportId=null;copied=false;
    $("responseBody").replaceChildren();$("usage").replaceChildren();$("responsePanel").classList.add("hidden");
    $("downloadBtn").classList.add("hidden");hideNotice("runNotice");chat?.refreshReportContext();
  }
  function chooseAgent(id){
    if(runInFlight)return false;
    const agent=agents.find(candidate=>candidate.id===id);if(!agent)return false;
    const wasIran=selectedAgent?.id==="iran-news";
    selectedAgent=agent;$("welcome").classList.add("hidden");$("workArea").classList.remove("hidden");
    $("task").readOnly=id==="iran-news";
    if(id==="iran-news")$("task").value="iran war";
    else if(wasIran && $("task").value==="iran war")$("task").value="";
    clearResult();renderSelectedAgent();renderAgents();refreshButtons();return true;
  }
  function renderScheduleOptions(){
    const value=$("scheduleAgent").value;$("scheduleAgent").replaceChildren();
    agents.forEach(agent=>{const option=newsElement("option","",agentName(agent.id));option.value=agent.id;$("scheduleAgent").append(option);});
    $("scheduleAgent").value=agents.some(agent=>agent.id===value) ? value : agents.some(agent=>agent.id==="geopolitical-morning") ? "geopolitical-morning" : agents[0]?.id || "";
  }
  function showWorkspace(name){
    if(name==="chat" && !authHeader)return;
    activeWorkspace=name==="chat" ? "chat" : "reports";
    $("reportsView").classList.toggle("hidden",activeWorkspace!=="reports");$("chatView").classList.toggle("hidden",activeWorkspace!=="chat");
    for(const workspace of ["reports","chat"]){const tab=$(workspace+"Tab");tab.setAttribute("aria-selected",String(activeWorkspace===workspace));tab.tabIndex=activeWorkspace===workspace ? 0 : -1;}
    if(activeWorkspace==="chat")chat?.refreshReportContext();
  }
  function refreshButtons(){
    $("loginBtn").textContent=t(loginBusy ? "login.checking" : "login.button");$("loginBtn").disabled=loginBusy;
    $("runBtn").textContent=t(runInFlight ? "task.running" : selectedAgent?.id==="iran-news" ? "newsdata.refresh" : "task.run");$("runBtn").disabled=runInFlight;
    $("runHint").textContent=t(runHintKey);
    $("copyBtn").textContent=t(copied ? "response.copied" : "response.copy");
    $("saveSchedule").textContent=t(scheduleBusy ? "schedule.saving" : editingScheduleId ? "schedule.saveEdit" : "schedule.save");$("saveSchedule").disabled=scheduleBusy;
    for(const button of $("responseBody").querySelectorAll(".news-page-button")){
      const p=lastResult?.pagination || {};
      button.disabled=runInFlight || (button.classList.contains("news-page-previous") ? !p.parent_report_id : newsDataLimitReached(lastResult) || (!p.has_more && !p.next_report_id) || p.next_report_status==="failed");
    }
  }
  function refreshLanguage(){
    renderHealth();renderAgents();renderSelectedAgent();renderScheduleOptions();refreshButtons();
    if(!editingScheduleId && !scheduleLanguageExplicit)$("scheduleLanguage").value=I18n.getLanguage();
    if(lastResult)displayResult(lastResult,{announce:false});
    if(dashboardCache)renderDashboard(dashboardCache);
    for(const [id,state] of notices){const el=$(id);el.textContent=t(state.key,noticeParams(state.params));}
    chat?.setLanguage();chat?.refreshReportContext();
  }
  function logout(){
    sessionVersion++;authHeader="";activeRequests.forEach(controller=>controller.abort());activeRequests.clear();
    chat?.logout();agents=[];selectedAgent=null;dashboardCache=null;editingScheduleId=null;archiveOffset=0;lastAttempt=null;newsPageAttempt=null;
    runInFlight=false;loginBusy=false;scheduleBusy=false;runHintKey="task.runHint";notices.clear();clearResult();
    for(const id of ["archiveList","scheduleList","agentList","skillTags"])$(id).replaceChildren();
    for(const id of ["selectedName","selectedDescription","selectedId","archivePageLabel","schedulerNotice"])$(id).textContent="";
    for(const id of ["dailyCount","dailyRemaining","dailyCost","dailyBudget","monthlyCost","monthlyBudget","providerStatus","scheduleStatus","newsdataCredits"])$(id).textContent="—";
    for(const id of ["password","username","search","task"])$(id).value="";
    $("scheduleAgent").replaceChildren();resetScheduleForm();
    for(const id of ["management","accountOverview","sessionBox","agentSection","workArea","workspaceTabs","headerLogoutBtn"])$(id).classList.add("hidden");
    for(const id of ["loginBox","welcome"])$(id).classList.remove("hidden");
    hideNotice("dashboardNotice");hideNotice("scheduleNotice");showWorkspace("reports");
    setNotice("loginNotice","login.prompt");renderAgents();refreshButtons();
  }
  async function login(){
    if(loginBusy)return;
    const username=$("username").value,password=$("password").value;
    if(!username || !password){setNotice("loginNotice","login.requiredFields","error");return;}
    const version=++sessionVersion, candidateAuth="Basic "+btoa(unescape(encodeURIComponent(username+":"+password)));
    const controller=new AbortController();activeRequests.add(controller);loginBusy=true;refreshButtons();
    try{
      const response=await fetch("/v1/agents",{headers:{Authorization:candidateAuth},cache:"no-store",signal:controller.signal});
      const data=await readJson(response);if(version!==sessionVersion)return;
      if(!response.ok){setNotice("loginNotice",response.status===401 ? "login.invalid" : response.status===503 ? "login.notConfigured" : "login.failed","error",{status:response.status});return;}
      authHeader=candidateAuth;agents=Array.isArray(data.agents) ? data.agents : [];$("password").value="";
      $("loginBox").classList.add("hidden");hideNotice("loginNotice");
      for(const id of ["sessionBox","agentSection","management","accountOverview","workspaceTabs","headerLogoutBtn"])$(id).classList.remove("hidden");
      renderAgents();renderScheduleOptions();resetScheduleForm();
      if(agents.length)chooseAgent(agents.find(agent=>agent.id==="geopolitical-morning")?.id || agents[0].id);
      else setNotice("loginNotice","login.noAgents","error");
      showWorkspace("reports");await Promise.allSettled([syncDashboard(),Promise.resolve(chat?.login())]);
    }catch(_){if(version===sessionVersion){authHeader="";setNotice("loginNotice","login.network","error");}}
    finally{activeRequests.delete(controller);if(version===sessionVersion){loginBusy=false;refreshButtons();}}
  }
  function displayResult(data,{announce=true}={}){
    lastResult=data;
    const localized=isNewsDataReport(data) ? data["response_"+I18n.getLanguage()] : null;
    lastResponse=typeof localized==="string" ? localized : typeof data.response==="string" ? data.response : "";latestReportId=data.report_id || null;
    const body=$("responseBody");
    if(Array.isArray(data.items) && Array.isArray(data.background_items))renderNewsReport(body,data);
    else{body.classList.remove("news-reading");body.dir="auto";body.lang=data.language || "";renderMarkdown(body,lastResponse || t("response.noText"));}
    $("usage").replaceChildren();
    const usage=data.usage || {},search=data.web_search || {};
    const newsCount=typeof data.item_count==="number" ? n(data.item_count)+(typeof data.target_count==="number" ? " / "+n(data.target_count) : "") : undefined;
    const searchLabel=search.research_incomplete ? t("search.partial") : search.status ? (I18n.has("search."+search.status) ? t("search."+search.status) : t("search.failed")) : undefined;
    const cells=[["usage.model",data.model],["usage.search",isNewsDataReport(data) ? "NewsData" : searchLabel],["usage.news",newsCount],
      ["usage.newsdataCredits",data.accounting?.newsdata_credits ? n(data.accounting.newsdata_credits) : undefined],
      ["usage.searchCalls",usage.web_search_calls==null ? undefined : n(usage.web_search_calls)],
      ["usage.input",(usage.prompt_tokens ?? usage.input_tokens)==null ? undefined : n(usage.prompt_tokens ?? usage.input_tokens)],
      ["usage.output",(usage.completion_tokens ?? usage.output_tokens)==null ? undefined : n(usage.completion_tokens ?? usage.output_tokens)],
      ["usage.cost",data.accounting?.estimated_cost_usd===undefined && data.estimated_cost_usd==null ? undefined : money(data.accounting?.estimated_cost_usd ?? data.estimated_cost_usd)]];
    cells.forEach(([key,value])=>{if(value!==undefined && value!==null)$("usage").append(newsElement("span","",t(key)+": "+value));});
    $("responsePanel").classList.remove("hidden");$("downloadBtn").classList.toggle("hidden",!latestReportId);
    if(announce){
      const empty=data.item_count===0,partial=typeof data.item_count==="number" && typeof data.target_count==="number" && data.item_count<data.target_count;
      setNotice("runNotice",empty ? "response.noNewsSaved" : partial ? "response.partialSaved" : "response.saved",empty || partial ? "" : "good");
    }
    chat?.refreshReportContext();
  }
  async function runTask(){
    if(runInFlight)return;
    if(!authHeader || !selectedAgent){setNotice("runNotice","task.signIn","error");return;}
    const task=selectedAgent.id==="iran-news" ? "iran war" : $("task").value.trim();if(!task){setNotice("runNotice","task.empty","error");return;}
    const agentId=selectedAgent.id,language=I18n.getLanguage(),version=sessionVersion;
    if(!lastAttempt || lastAttempt.agent!==agentId || lastAttempt.task!==task || lastAttempt.language!==language)
      lastAttempt={agent:agentId,task,language,key:crypto.randomUUID()};
    const attempt=lastAttempt;runInFlight=true;runHintKey="task.runningHint";refreshButtons();clearResult();
    try{
      const data=await apiJson("/v1/agents/"+encodeURIComponent(agentId)+"/run","POST",{task,language},{"Idempotency-Key":attempt.key});
      if(version!==sessionVersion || !authHeader)return;
      lastAttempt=null;newsPageAttempt=null;displayResult(data);$("task").value=agentId==="iran-news" ? "iran war" : "";runHintKey="task.savedHint";
    }catch(error){
      if(version!==sessionVersion || !authHeader)return;
      if(error.status && error.code!=="report_in_progress")lastAttempt=null;
      if(error.status)setNotice("runNotice","task.failed","error",{status:error.status,message:error});
      else setNotice("runNotice","task.connectionLost","error");
    }finally{
      if(version===sessionVersion){runInFlight=false;refreshButtons();if(authHeader)await syncDashboard();}
    }
  }
  async function changeNewsPage(direction){
    if(runInFlight || !authHeader || !isNewsDataReport(lastResult) || !latestReportId)return;
    const previous=lastResult,parentId=latestReportId,pagination=previous.pagination || {},version=sessionVersion;
    if(direction!=="previous" && newsDataLimitReached(previous))return;
    const savedId=direction==="previous" ? pagination.parent_report_id : pagination.next_report_id;
    if(!savedId && (direction==="previous" || !pagination.has_more))return;
    runInFlight=true;refreshButtons();setNotice("runNotice","newsdata.loading");
    try{
      let data;
      if(savedId){
        const saved=await apiJson("/v1/reports/"+encodeURIComponent(savedId));
        if(!saved.result)throw makeError({detail:{code:saved.status==="pending" ? "report_in_progress" : "report_already_attempted"}},409);
        data={...saved.result,report_id:saved.report_id,task:saved.task,language:saved.language || saved.result.language,
          pagination:{...(saved.result.pagination || {}),...(saved.pagination || {})},estimated_cost_usd:saved.estimated_cost_usd};
      }else{
        if(!newsPageAttempt || newsPageAttempt.parent!==parentId)newsPageAttempt={parent:parentId,key:crypto.randomUUID()};
        data=await apiJson("/v1/agents/iran-news/run","POST",{
          task:previous.task || "iran war",language:previous.language==="en" ? "en" : "ar",continue_from_report_id:parentId
        },{"Idempotency-Key":newsPageAttempt.key});
      }
      if(version!==sessionVersion || !authHeader)return;
      if(!isNewsDataReport(data))throw makeError({detail:{code:"invalid_newsdata_continuation"}},409);
      newsPageAttempt=null;displayResult(data);$("responsePanel").scrollIntoView({behavior:"smooth",block:"start"});
    }catch(error){
      if(version!==sessionVersion || !authHeader)return;
      setError("runNotice",error);
    }finally{
      if(version===sessionVersion){runInFlight=false;refreshButtons();if(authHeader)await syncDashboard();}
    }
  }
  function smallButton(label,action){const button=newsElement("button","textbutton",label);button.type="button";button.addEventListener("click",action);return button;}
  async function downloadReport(id,format="md",language=null){
    try{
      const blob=await authenticatedRequest("/v1/reports/"+encodeURIComponent(id)+"/download?format="+encodeURIComponent(format)+(language ? "&language="+encodeURIComponent(language) : ""),"GET",undefined,{},true);
      const url=URL.createObjectURL(blob),anchor=document.createElement("a");
      anchor.href=url;anchor.download="AI_HUB_Report_"+id+"."+format;document.body.append(anchor);anchor.click();anchor.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(error){setError("dashboardNotice",error);}
  }
  function renderArchive(rows){
    $("archiveList").replaceChildren();$("archivePageLabel").textContent=rows.length ? n(archiveOffset+1)+"–"+n(archiveOffset+rows.length) : t("archive.noResults");
    $("archivePrev").disabled=archiveOffset===0;$("archiveNext").disabled=rows.length<10;
    if(!rows.length){$("archiveList").append(newsElement("p","hint",t("archive.empty")));return;}
    rows.forEach(row=>{
      const article=newsElement("article","report-row"),title=newsElement("h4","",String(row.task || t("archive.report")).slice(0,160));title.dir="auto";
      const meta=newsElement("div","report-meta");
      const state=newsElement("span","pill"+(row.status==="failed" ? " error" : row.status==="pending" ? " warn" : ""),t(I18n.has("archive."+row.status) ? "archive."+row.status : "incomplete"));
      meta.append(state,newsElement("span","",agentName(row.agent_id)+" · "+localDate(row.created_at)));
      const cost=newsElement("p","",t("archive.cost",{trigger:t(row.trigger==="scheduled" ? "archive.scheduled" : "archive.manual"),amount:money(row.estimated_cost_usd)}));
      if(row.error_code)cost.append(document.createTextNode(" · "+prettyError({detail:{code:row.error_code}},503)));
      const actions=newsElement("div","actions");
      actions.append(smallButton(t("archive.view"),async()=>{
        if(runInFlight)return;
        try{
          const saved=await apiJson("/v1/reports/"+encodeURIComponent(row.report_id));
          if(runInFlight)return;
          if(!saved.result){setNotice("dashboardNotice",saved.status==="pending" ? "archive.inProgress" : "archive.noCompletedResult");return;}
          showWorkspace("reports");chooseAgent(saved.agent_id);
          displayResult({...saved.result,report_id:saved.report_id,task:saved.task,language:saved.language || saved.result.language,
            pagination:{...(saved.result.pagination || {}),...(saved.pagination || {})},estimated_cost_usd:saved.estimated_cost_usd});
          $("responsePanel").scrollIntoView({behavior:"smooth",block:"start"});
        }catch(error){setError("dashboardNotice",error);}
      }),smallButton(t("archive.download"),()=>downloadReport(row.report_id)),smallButton("JSON",()=>downloadReport(row.report_id,"json")));
      article.append(title,meta,cost,actions);$("archiveList").append(article);
    });
  }
  function resetScheduleForm(){
    editingScheduleId=null;scheduleLanguageExplicit=false;$("scheduleForm").reset();$("cancelScheduleEdit").classList.add("hidden");
    if(agents.some(agent=>agent.id==="geopolitical-morning"))$("scheduleAgent").value="geopolitical-morning";
    if($("scheduleLanguage"))$("scheduleLanguage").value=I18n.getLanguage();
    refreshButtons();
  }
  function renderSchedules(rows,occurrences=[]){
    $("scheduleList").replaceChildren();
    if(!rows.length){$("scheduleList").append(newsElement("p","hint",t("schedule.empty")));return;}
    rows.forEach(row=>{
      const id=row.schedule_id || row.id,article=newsElement("article","schedule-row");
      const title=newsElement("h4","");const name=newsElement("bdi","",row.name || agentName(row.agent_id));
      title.append(name,document.createTextNode(" · "+t(row.enabled ? "schedule.enabled" : "schedule.disabled")));
      const details=newsElement("p","",t("schedule.details",{agent:agentName(row.agent_id),times:(row.times || []).join(", ")}));
      if(row.language)details.append(document.createTextNode(" · "+t("schedule.languageDisplay",{language:t(row.language==="en" ? "language.english" : "language.arabic")})));
      const task=newsElement("p","",String(row.task || "").slice(0,140));task.dir="auto";
      const actions=newsElement("div","actions");
      actions.append(smallButton(t("schedule.edit"),()=>{
        editingScheduleId=id;scheduleLanguageExplicit=true;$("scheduleName").value=row.name || "";$("scheduleAgent").value=row.agent_id;
        $("scheduleTask").value=row.task || "";$("scheduleTimes").value=(row.times || []).join(", ");$("scheduleEnabled").checked=!!row.enabled;
        if($("scheduleLanguage"))$("scheduleLanguage").value=row.language==="en" ? "en" : "ar";
        $("cancelScheduleEdit").classList.remove("hidden");refreshButtons();$("scheduleName").focus();
      }),smallButton(t(row.enabled ? "schedule.pause" : "schedule.enable"),async()=>{
        try{await apiJson("/v1/schedules/"+encodeURIComponent(id),"PATCH",{enabled:!row.enabled});await syncDashboard();}catch(error){setError("scheduleNotice",error);}
      }),smallButton(t("schedule.delete"),async()=>{
        if(!window.confirm(t("schedule.confirmDelete")))return;
        try{await apiJson("/v1/schedules/"+encodeURIComponent(id),"DELETE");if(editingScheduleId===id)resetScheduleForm();await syncDashboard();}catch(error){setError("scheduleNotice",error);}
      }));
      article.append(title,details,task,actions);
      const last=occurrences.find(occurrence=>occurrence.schedule_id===id);
      if(last)article.append(newsElement("p","",t("schedule.last",{date:localDate(last.due_at),status:t(I18n.has("schedule.state."+last.status) ? "schedule.state."+last.status : "incomplete")})));
      $("scheduleList").append(article);
    });
  }
  function renderDashboard(data){
    const {usage,system,reports,schedules,occurrences}=data;
    $("dailyCount").textContent=n(usage.daily.attempts)+" / "+n(usage.daily.limit);
    $("dailyRemaining").textContent=t("overview.remaining",{count:n(usage.daily.remaining)});
    $("dailyCost").textContent=money(usage.daily.estimated_cost_usd);$("dailyBudget").textContent=t("overview.budget",{amount:money(usage.daily.budget_usd)});
    $("monthlyCost").textContent=money(usage.monthly.estimated_cost_usd);$("monthlyBudget").textContent=t("overview.budget",{amount:money(usage.monthly.budget_usd)});
    $("providerStatus").textContent=system.iran_news?.configured ? "NewsData + "+(system.model_configured ? "OpenAI" : t("overview.pendingTranslation")) : system.model_configured ? "OpenAI" : t("overview.pending");
    $("scheduleStatus").textContent=system.iran_news ? t("overview.newsdataConfiguration",{news:t(system.iran_news.configured ? "configured" : "notConfigured"),translation:t(system.iran_news.translation_configured ? "configured" : "notConfigured")}) : t("overview.searchPricing",{search:t(system.search_configured ? "configured" : "notConfigured"),pricing:t(system.pricing_configured ? "configured" : "incomplete")});
    const credits=usage.provider_credits?.newsdata;
    $("newsdataCredits").textContent=credits ? t("overview.newsdataCredits",{used:n(credits.daily_accounted || 0),limit:n(credits.daily_limit || 0),remaining:n(credits.daily_remaining || 0)}) : "—";
    $("schedulerNotice").textContent=t(system.scheduler_running ? "schedule.running" : "schedule.stopped");
    renderArchive(reports.reports || []);renderSchedules(schedules.schedules || [],occurrences.occurrences || []);
  }
  async function syncDashboard(){
    if(!authHeader)return;
    const version=sessionVersion;
    try{
      const [usage,system,reports,schedules,occurrences]=await Promise.all([
        apiJson("/v1/usage"),apiJson("/v1/system"),apiJson("/v1/reports?limit=10&offset="+archiveOffset),
        apiJson("/v1/schedules"),apiJson("/v1/schedule-occurrences?limit=100")]);
      if(version!==sessionVersion || !authHeader)return;
      dashboardCache={usage,system,reports,schedules,occurrences};renderDashboard(dashboardCache);
    }catch(error){if(version===sessionVersion)setError("dashboardNotice",error);}
  }
  async function saveSchedule(event){
    event.preventDefault();if(scheduleBusy)return;
    const times=$("scheduleTimes").value.replace(/،/g,",").split(",").map(value=>value.trim()).filter(Boolean);
    if(!times.length || times.some(value=>!/^([01]\d|2[0-3]):[0-5]\d$/.test(value))){setNotice("scheduleNotice","schedule.invalidTimes","error");return;}
    const data={name:$("scheduleName").value.trim(),agent_id:$("scheduleAgent").value,task:$("scheduleTask").value.trim(),
      times,enabled:$("scheduleEnabled").checked,language:$("scheduleLanguage")?.value || I18n.getLanguage()};
    if(!data.task){setNotice("scheduleNotice","schedule.emptyTask","error");return;}
    const version=sessionVersion;scheduleBusy=true;refreshButtons();
    try{
      await apiJson(editingScheduleId ? "/v1/schedules/"+encodeURIComponent(editingScheduleId) : "/v1/schedules",editingScheduleId ? "PATCH" : "POST",data);
      if(version!==sessionVersion)return;
      resetScheduleForm();setNotice("scheduleNotice","schedule.saved","good");await syncDashboard();
    }catch(error){if(version===sessionVersion)setError("scheduleNotice",error);}
    finally{if(version===sessionVersion){scheduleBusy=false;refreshButtons();}}
  }
  function getCurrentReport(){
    if(!authHeader || !latestReportId || !lastResult || $("responsePanel").classList.contains("hidden"))return null;
    return {id:latestReportId,title:agentName(lastResult.agent_id || selectedAgent?.id)};
  }
  if(window.AIHubChat?.create)chat=window.AIHubChat.create({
    root:$("chatRoot"),request:apiJson,renderMarkdown,getLanguage:()=>I18n.getLanguage(),getCurrentReport,
    getSelectedAgent:()=>selectedAgent ? {id:selectedAgent.id,name:agentName(selectedAgent.id)} : null
  });
  $("loginBtn").addEventListener("click",login);
  for(const id of ["username","password"])$(id).addEventListener("keydown",event=>{if(event.key==="Enter")$("loginBtn").click();});
  $("logoutBtn").addEventListener("click",logout);$("headerLogoutBtn").addEventListener("click",logout);
  $("search").addEventListener("input",renderAgents);$("clearBtn").addEventListener("click",clearResult);
  $("runBtn").addEventListener("click",runTask);
  $("copyBtn").addEventListener("click",async()=>{
    if(!lastResponse)return;
    const version=sessionVersion;
    try{await navigator.clipboard.writeText(lastResponse);if(version!==sessionVersion)return;copied=true;refreshButtons();setTimeout(()=>{if(version===sessionVersion){copied=false;refreshButtons();}},1400);}
    catch(_){if(version===sessionVersion)setNotice("runNotice","response.copyFailed","error");}
  });
  $("scheduleForm").addEventListener("submit",saveSchedule);
  $("scheduleLanguage").addEventListener("change",()=>{scheduleLanguageExplicit=true;});
  $("cancelScheduleEdit").addEventListener("click",resetScheduleForm);
  $("downloadBtn").addEventListener("click",()=>latestReportId && downloadReport(latestReportId,"md",isNewsDataReport(lastResult) ? I18n.getLanguage() : null));
  $("refreshDashboard").addEventListener("click",syncDashboard);
  $("archivePrev").addEventListener("click",()=>{archiveOffset=Math.max(0,archiveOffset-10);syncDashboard();});
  $("archiveNext").addEventListener("click",()=>{archiveOffset+=10;syncDashboard();});
  $("worldNewsPreset").addEventListener("click",()=>{if(chooseAgent("geopolitical-morning")){$("task").value=t("task.worldPrompt");$("task").focus();}});
  $("iranNewsPreset").addEventListener("click",()=>{if(chooseAgent("iran-news")){$("task").value=t("task.iranPrompt");$("task").focus();}});
  $("languageAR").addEventListener("click",()=>I18n.setLanguage("ar"));
  $("languageEN").addEventListener("click",()=>I18n.setLanguage("en"));
  window.addEventListener("aihub:languagechange",refreshLanguage);
  window.addEventListener("aihub:chatupdated",()=>{if(authHeader)syncDashboard();});
  for(const name of ["reports","chat"]){
    const tab=$(name+"Tab");tab.addEventListener("click",()=>showWorkspace(name));
    tab.addEventListener("keydown",event=>{
      if(["ArrowLeft","ArrowRight","Home","End"].includes(event.key)){
        event.preventDefault();const target=event.key==="Home" ? "reports" : event.key==="End" ? "chat" : activeWorkspace==="reports" ? "chat" : "reports";
        showWorkspace(target);$(target+"Tab").focus();
      }
    });
  }
  I18n.apply();refreshButtons();setNotice("loginNotice","login.prompt");
  fetchHealth();
})();
