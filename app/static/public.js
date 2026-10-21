(function(){
  "use strict";
  const I18n=window.AIHubI18n;
  if(!I18n)return;
  const $=id=>document.getElementById(id),t=(key,params)=>I18n.t(key,params),n=value=>I18n.number(value);
  I18n.register({
    ar:{
      "public.brand":"أخبار ومعرفة ومحادثة",
      "public.connecting":"جارٍ الاتصال","public.ready":"جاهز للبحث","public.offline":"تعذر الاتصال",
      "public.sections":"أقسام المنصة","public.searchTab":"البحث والوكلاء","public.chatTab":"محادثة AI HUB",
      "public.assistants":"اختر وكيلك","public.sidebarNote":"اختر المجال الذي تحتاجه، واكتب طلبك، ثم اضغط Enter.",
      "public.eyebrow":"مساحة المعرفة","public.iranDescription":"تابع تغطيات حرب إيران من مصادر متنوعة، بعناوين واضحة وملخصات وروابط للمقالات الأصلية.",
      "public.searchLabel":"ما الذي تريد البحث عنه؟","public.placeholder":"اكتب سؤالك أو طلبك هنا…",
      "public.keyboard":"Enter للبحث · Shift + Enter لسطر جديد","public.search":"بحث","public.searching":"جارٍ البحث…",
      "public.iranScope":"حتى 50 نتيجة عن iran war خلال آخر 12 ساعة، مرتبة من الأحدث. العربية والإنجليزية متاحتان.",
      "public.results":"نتائج البحث","public.discuss":"ناقش النتائج","public.emptyTitle":"معرفة أوضح، من مصادرها",
      "public.emptyDescription":"اضغط Enter أو «بحث» لعرض النتائج. ويمكنك مناقشتها مع وكيل AI HUB.",
      "public.benefitOne":"مصادر وروابط مباشرة","public.benefitTwo":"ملخصات بالعربية والإنجليزية","public.benefitThree":"حتى 50 نتيجة للأخبار",
      "public.footer":"افتح المصادر الأصلية للتفاصيل. قد تخطئ الإجابات الآلية، فراجع المعلومات المهمة.",
      "public.loading":"جارٍ جلب النتائج وإعدادها…","public.pageLoading":"جارٍ فتح الصفحة…",
      "public.error":"تعذر إكمال الطلب الآن. حاول مرة أخرى بعد قليل.","public.unavailable":"الخدمة غير متاحة مؤقتًا. حاول مرة أخرى لاحقًا.",
      "public.network":"تعذر تأكيد اكتمال الطلب. اضغط بحث مرة أخرى للتحقق من المحاولة نفسها.",
      "public.invalid":"اكتب سؤالك أو طلبك أولًا.","public.expired":"تعذر استعادة هذه الجلسة. حدّث الصفحة للمتابعة.",
      "public.copyFailed":"يمكنك تحديد النص ونسخه يدويًا.","public.sessionRestored":"استُعيدت آخر نتيجة في هذه الجلسة.",
      "chat.scopeHint":"تستخدم المحادثة رسائلك والتقرير المرفق. لجلب أخبار جديدة، استخدم قسم البحث.",
      "chat.newAttemptHint":"لم تكتمل المحاولة السابقة. الإرسال مرة أخرى يبدأ محاولة جديدة.",
      "chat.errorBudget":"الخدمة مشغولة حاليًا. حاول مرة أخرى لاحقًا.",
      "chat.errorAuth":"حدّث الصفحة لاستعادة جلسة المحادثة.",
      "chat.errorUnconfigured":"المحادثة غير متاحة مؤقتًا. حاول مرة أخرى لاحقًا."
    },
    en:{
      "public.brand":"News, knowledge and conversation",
      "public.connecting":"Connecting","public.ready":"Ready to search","public.offline":"Connection unavailable",
      "public.sections":"Workspace sections","public.searchTab":"Search and agents","public.chatTab":"Chat with AI HUB",
      "public.assistants":"Choose an agent","public.sidebarNote":"Choose a topic, write your request and press Enter.",
      "public.eyebrow":"YOUR KNOWLEDGE WORKSPACE","public.iranDescription":"Follow Iran war coverage from a range of publishers, with clear headlines, summaries and links to original articles.",
      "public.searchLabel":"What would you like to find?","public.placeholder":"Write your question or request here…",
      "public.keyboard":"Enter to search · Shift + Enter for a new line","public.search":"Search","public.searching":"Searching…",
      "public.iranScope":"The first 50 results for iran war over the latest 12 hours, newest first. Available in Arabic and English.",
      "public.results":"Search results","public.discuss":"Discuss results","public.emptyTitle":"Knowledge, closer to the source",
      "public.emptyDescription":"Press Enter or Search to see results. Then explore them with the AI HUB agent.",
      "public.benefitOne":"Direct source links","public.benefitTwo":"Arabic and English summaries","public.benefitThree":"Up to 50 news results",
      "public.footer":"Open original sources for details. AI responses can contain mistakes; check important information.",
      "public.loading":"Finding and preparing results…","public.pageLoading":"Opening the page…",
      "public.error":"The request could not be completed right now. Please try again shortly.","public.unavailable":"The service is temporarily unavailable. Please try again later.",
      "public.network":"Completion could not be confirmed. Press Search again to check the same attempt.",
      "public.invalid":"Write your question or request first.","public.expired":"This session could not be restored. Refresh the page to continue.",
      "public.copyFailed":"You can select and copy the text manually.","public.sessionRestored":"Your last result in this session was restored.",
      "chat.scopeHint":"Chat uses your messages and the attached report. Use Search to retrieve fresh news.",
      "chat.newAttemptHint":"The previous attempt did not finish. Sending again starts a new attempt.",
      "chat.errorBudget":"The service is busy right now. Please try again later.",
      "chat.errorAuth":"Refresh the page to restore your chat session.",
      "chat.errorUnconfigured":"Chat is temporarily unavailable. Please try again later."
    }
  });
  let ready=false,busy=false,agents=[],selected=null,lastResult=null,lastText="",attempt=null,pageAttempt=null,chat=null;
  let noticeState=null,activeTab="reports";
  const LAST_REPORT_KEY="aihub.public.lastReport";
  const isNews=data=>data?.provider==="newsdata"&&data.agent_id==="iran-news";
  const el=(tag,cls,text)=>{const node=document.createElement(tag);if(cls)node.className=cls;if(text!==undefined)node.textContent=String(text);return node;};
  const localDate=value=>{const date=new Date(value);return Number.isNaN(date.getTime())?"":date.toLocaleString(I18n.getLocale(),{timeZone:"Asia/Dubai",dateStyle:"medium",timeStyle:"short"});};
  function nameOf(agent){return I18n.has("agent."+agent.id+".name")?t("agent."+agent.id+".name"):(I18n.getLanguage()==="en"?agent.name_en:agent.name)||agent.id;}
  function setNotice(key,kind=""){noticeState={key,kind};$("publicNotice").textContent=t(key);$("publicNotice").className="notice"+(kind?" "+kind:"");}
  function hideNotice(){noticeState=null;$("publicNotice").textContent="";$("publicNotice").className="notice hidden";}
  function publicError(data,status){
    const code=typeof data?.detail?.code==="string"?data.detail.code:"";
    const key=code==="newsdata_result_limit"?"error.newsdata_result_limit":status===401?"public.expired":status===429||status===503?"public.unavailable":status===422?"public.invalid":"public.error";
    const detail={code};
    for(const [name,pattern] of Object.entries({turn_id:/^cht_[a-f0-9]{32}$/,conversation_id:/^conv_[a-f0-9]{32}$/,report_id:/^rpt_[a-f0-9]{32}$/})){
      const value=data?.detail?.[name];if(typeof value==="string"&&pattern.test(value))detail[name]=value;
    }
    const error=new Error(t(key));error.code=code;error.status=status;error.detail=detail;error.i18nKey=key;return error;
  }
  async function request(path,method="GET",body,extra={}){
    let target;try{target=new URL(path,window.location.origin);}catch(_){throw publicError({},400);}
    if(typeof path!=="string"||!path.startsWith("/v1/public/")||/[\u0000-\u0020\u007f\\]/.test(path)||target.origin!==window.location.origin||!target.pathname.startsWith("/v1/public/"))throw publicError({},400);
    const headers={};if(body!==undefined)headers["Content-Type"]="application/json";
    if(typeof extra["Idempotency-Key"]==="string")headers["Idempotency-Key"]=extra["Idempotency-Key"];
    const response=await fetch(target.pathname+target.search,{method,headers,credentials:"same-origin",cache:"no-store",redirect:"error",body:body===undefined?undefined:JSON.stringify(body)});
    let data={};try{const text=await response.text();data=text?JSON.parse(text):{};}catch(_){throw publicError({},502);}
    if(!response.ok)throw publicError(data,response.status);return data;
  }
  function safeArticleLink(value){
    if(typeof value!=="string"||/[\u0000-\u0020\u007f\\]/.test(value))return null;
    try{const url=new URL(value),host=url.hostname.toLowerCase().replace(/\.$/,"");
      if(!["https:","http:"].includes(url.protocol)||url.username||url.password||url.port||!host.includes(".")||!/^[a-z0-9.-]+$/.test(host)||/^[\d.]+$/.test(host)||/(?:^|\.)(?:localhost|local|internal|lan|home|invalid)$/.test(host))return null;
      return url.href;
    }catch(_){return null;}
  }
  function appendInline(parent, source){
    const text = String(source || "");
    const pattern = /\[([^\]]+)\]\(<?(https?:\/\/[^)\s>]+)>?\)|\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|(https?:\/\/[^\s<>]+)/g;
    let cursor = 0, match;
    while((match = pattern.exec(text)) !== null){
      if(match.index > cursor) parent.append(document.createTextNode(text.slice(cursor, match.index)));
      if(match[1] !== undefined){
        const a = document.createElement("a");
        a.textContent = match[1]; a.href = safeArticleLink(match[2]) || "#"; a.target = "_blank"; a.rel = "noopener noreferrer";
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
        const a = document.createElement("a"); a.textContent = url; a.href = safeArticleLink(url) || "#"; a.target = "_blank"; a.rel = "noopener noreferrer";
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
  function beforeCount(data){const p=data?.pagination||{};return Number.isSafeInteger(p.results_before_page)&&p.results_before_page>=0?p.results_before_page:Math.max(0,(Number(p.page_index)||1)-1)*(Number(p.page_size)||10);}
  function reachedLimit(data){const p=data?.pagination||{};return p.limit_reached===true||(Number.isSafeInteger(p.results_consumed)?p.results_consumed>=50:beforeCount(data)+(data?.newsdata?.returned_item_count??data?.items?.length??0)>=50);}
  function nextUnavailable(data){const p=data?.pagination||{};return reachedLimit(data)||(!p.has_more&&!p.next_report_id)||p.next_report_status==="failed";}
  function renderNews(target,data){
    target.replaceChildren();target.className="response-body news-reading";target.dir=I18n.getDirection();target.lang=I18n.getLanguage();
    const p=data.pagination||{},items=(Array.isArray(data.items)?data.items:[]).filter(x=>x&&typeof x==="object").slice(0,Math.max(0,50-beforeCount(data)));
    const intro=el("header","news-report-intro");
    intro.append(el("p","news-count",t("newsdata.pageCount",{page:n(p.page_index||1),count:n(items.length)})),el("p","news-result-limit",t("newsdata.limitNote")));
    if(data.generated_at)intro.append(el("p","news-prepared",t("news.prepared",{date:localDate(data.generated_at)})));
    target.append(intro);
    for(const item of items){
      const translated=I18n.getLanguage()==="ar"&&item.translation_status==="translated"&&typeof item.headline_ar==="string"&&item.headline_ar.trim();
      const article=el("article","news-item");article.lang=translated?"ar":"en";article.dir=translated?"rtl":"ltr";
      const title=translated?item.headline_ar:item.headline_en||item.headline||t("news.fallbackHeadline");
      const summary=translated?item.summary_ar:item.summary_en||item.summary;
      article.append(el("h3","news-headline",title),el("p","news-summary",summary||t("news.noSummary")));
      const meta=el("div","news-byline");meta.dir=I18n.getDirection();meta.append(el("bdi","news-publisher",item.publisher||""));
      if(item.published_date){const stamp=el("time","news-published",localDate(item.published_date));stamp.dateTime=item.published_date;meta.append(stamp);}
      article.append(meta);const url=safeArticleLink(item.url);
      if(url){const link=el("a","news-source-link",t("news.openSource")+" ↗");link.href=url;link.target="_blank";link.rel="noopener noreferrer";link.dir=I18n.getDirection();article.append(link);}
      if(I18n.getLanguage()==="ar"&&!translated){const note=el("p","news-translation-label",t("newsdata.originalFallback"));note.dir="rtl";article.append(note);}
      target.append(article);
    }
    if(!items.length)target.append(el("p","news-empty",t("newsdata.empty")));
    const nav=el("nav","news-pagination");nav.setAttribute("aria-label",t("newsdata.navigation"));
    const previous=el("button","secondary news-page-button news-page-previous",t("newsdata.previous"));previous.type="button";previous.disabled=busy||!p.parent_report_id;previous.addEventListener("click",()=>changePage("previous"));
    const next=el("button","primary news-page-button news-page-next",t("newsdata.next"));next.type="button";next.disabled=busy||nextUnavailable(data);next.addEventListener("click",()=>changePage("next"));
    nav.append(previous,el("span","news-page-index",t("newsdata.page",{page:n(p.page_index||1)})),next);target.append(nav);
    target.append(el("p","news-section-note",t(reachedLimit(data)?"newsdata.limitReached":p.next_report_status==="failed"?"newsdata.failedPage":"newsdata.pageNote")));
    if(data.freshness?.plan!=="paid")target.append(el("p","news-coverage-note",t(data.freshness?.plan==="free"?"newsdata.freeDelay":"newsdata.planUnknown")));
    const notes=el("details","news-notes");notes.append(el("summary","",t("news.notes")),el("p","",t("newsdata.provenance")));target.append(notes);
  }
  function unwrap(data){return data?.result?{...data.result,report_id:data.report_id,task:data.task,language:data.language||data.result.language,pagination:{...(data.result.pagination||{}),...(data.pagination||{})}}:data;}
  function showResult(input){
    const data=unwrap(input);if(!data||typeof data.response!=="string"||typeof data.report_id!=="string")throw publicError({},502);
    lastResult=data;lastText=isNews(data)?data["response_"+I18n.getLanguage()]||data.response:data.response;
    $("publicEmpty").classList.add("hidden");$("publicResults").classList.remove("hidden");
    const body=$("publicResultBody");
    if(isNews(data))renderNews(body,data);else{body.className="response-body";body.dir=data.language==="en"?"ltr":"rtl";body.lang=data.language||I18n.getLanguage();renderMarkdown(body,lastText);}
    try{window.localStorage.setItem(LAST_REPORT_KEY,data.report_id);}catch(_){}
    chat?.refreshReportContext();
  }
  function renderAgents(){
    const list=$("publicAgentList");list.replaceChildren();$("publicAgentCount").textContent=n(agents.length);
    for(const agent of agents){const button=el("button","public-agent"+(agent.id===selected?.id?" active":""));button.type="button";button.disabled=busy;button.setAttribute("aria-pressed",String(agent.id===selected?.id));
      button.append(el("span","public-agent-number",agent.number||String(agents.indexOf(agent)+1).padStart(2,"0")),el("span","",nameOf(agent)));button.addEventListener("click",()=>chooseAgent(agent.id));list.append(button);}
  }
  function renderSelection(){
    if(!selected)return;$("publicAgentName").textContent=nameOf(selected);
    $("publicAgentDescription").textContent=selected.id==="iran-news"?t("public.iranDescription"):I18n.has("agent."+selected.id+".description")?t("agent."+selected.id+".description"):selected.description||"";
    $("publicTask").readOnly=selected.id==="iran-news";$("publicQueryNote").classList.toggle("hidden",selected.id!=="iran-news");
    $("publicSearchButton").disabled=!ready||busy||!selected.enabled;
    $("publicSearchButton").replaceChildren(el("span","",t(busy?"public.searching":"public.search")),el("span","","↗"));
  }
  function chooseAgent(id){
    if(busy)return;const agent=agents.find(a=>a.id===id);if(!agent)return;
    const changed=selected?.id!==id;selected=agent;
    if(changed){$("publicTask").value=id==="iran-news"?"iran war":"";attempt=null;pageAttempt=null;lastResult=null;lastText="";$("publicResults").classList.add("hidden");$("publicEmpty").classList.remove("hidden");hideNotice();}
    renderAgents();renderSelection();chat?.refreshReportContext();
  }
  function setBusy(value){busy=value;$("publicReports").setAttribute("aria-busy",String(value));renderAgents();renderSelection();if(lastResult)showResult(lastResult);}
  async function search(){
    if(!ready||busy||!selected)return;
    const task=selected.id==="iran-news"?"iran war":$("publicTask").value.trim(),language=I18n.getLanguage();
    if(!task){setNotice("public.invalid","error");$("publicTask").focus();return;}
    const identity=JSON.stringify([selected.id,task,language]);if(!attempt||attempt.identity!==identity)attempt={identity,key:crypto.randomUUID()};
    const current=attempt;setBusy(true);setNotice("public.loading");
    try{const result=await request("/v1/public/agents/"+encodeURIComponent(selected.id)+"/run","POST",{task,language},{"Idempotency-Key":current.key});showResult(result);attempt=null;pageAttempt=null;hideNotice();}
    catch(error){if(error.status&&!["report_in_progress","public_in_progress"].includes(error.code))attempt=null;setNotice(error.i18nKey||"public.network","error");}
    finally{setBusy(false);}
  }
  async function changePage(direction){
    if(!ready||busy||!isNews(lastResult))return;
    const data=lastResult,p=data.pagination||{};
    if(direction!=="previous"&&nextUnavailable(data))return;
    const savedId=direction==="previous"?p.parent_report_id:p.next_report_id;
    if(direction==="previous"&&!savedId)return;
    setBusy(true);setNotice("public.pageLoading");
    try{let next;
      if(savedId)next=await request("/v1/public/reports/"+encodeURIComponent(savedId));
      else{if(!pageAttempt||pageAttempt.parent!==data.report_id)pageAttempt={parent:data.report_id,key:crypto.randomUUID()};
        next=await request("/v1/public/agents/iran-news/run","POST",{task:data.task||"iran war",language:data.language==="en"?"en":"ar",continue_from_report_id:data.report_id},{"Idempotency-Key":pageAttempt.key});}
      showResult(next);pageAttempt=null;hideNotice();$("publicResults").scrollIntoView({behavior:"smooth",block:"start"});
    }catch(error){setNotice(error.i18nKey||"public.network","error");}finally{setBusy(false);}
  }
  function showTab(tab){
    activeTab=tab==="chat"?"chat":"reports";
    $("publicReports").classList.toggle("hidden",activeTab!=="reports");$("publicChat").classList.toggle("hidden",activeTab!=="chat");
    for(const name of ["Reports","Chat"]){const button=$("public"+name+"Tab"),active=name.toLowerCase()===activeTab;button.classList.toggle("active",active);button.setAttribute("aria-selected",String(active));}
    if(activeTab==="chat")chat?.refreshReportContext();
  }
  function currentReport(){return lastResult?{id:lastResult.report_id,title:lastResult.task||(selected?nameOf(selected):t("public.results"))}:null;}
  async function copyResult(){if(!lastText)return;try{await navigator.clipboard.writeText(lastText);$("publicCopy").textContent=t("response.copied");}catch(_){setNotice("public.copyFailed");}}
  function downloadResult(){if(!lastText)return;const blob=new Blob([lastText],{type:"text/markdown;charset=utf-8"}),url=URL.createObjectURL(blob),link=el("a");link.href=url;link.download="AI_HUB_Report_"+I18n.getLanguage()+".md";document.body.append(link);link.click();link.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function refreshLanguage(){I18n.apply();renderAgents();renderSelection();$("publicStatus").textContent=t(ready?"public.ready":"public.connecting");if(lastResult)showResult(lastResult);if(noticeState)setNotice(noticeState.key,noticeState.kind);chat?.setLanguage();}
  async function start(){
    I18n.apply();
    try{const data=await request("/v1/public/bootstrap");agents=Array.isArray(data.agents)?data.agents.filter(a=>a&&typeof a.id==="string"&&a.enabled!==false):[];ready=true;
      $("publicStatus").textContent=t("public.ready");$("publicStatus").classList.add("ready");chooseAgent(agents.find(a=>a.id==="iran-news")?.id||agents[0]?.id);
      if(window.AIHubChat){chat=window.AIHubChat.create({root:$("publicChatRoot"),request,endpoint:"/v1/public/chat/conversations",renderMarkdown,getLanguage:()=>I18n.getLanguage(),getCurrentReport:currentReport});await chat.login();}
      let lastId;try{lastId=window.localStorage.getItem(LAST_REPORT_KEY);}catch(_){}
      if(lastId&&/^rpt_[A-Za-z0-9_-]{1,96}$/.test(lastId)){try{const record=await request("/v1/public/reports/"+encodeURIComponent(lastId));if(record.status==="succeeded"){chooseAgent(record.agent_id);showResult(record);}}catch(_){try{window.localStorage.removeItem(LAST_REPORT_KEY);}catch(_){}}}
    }catch(error){ready=false;$("publicStatus").textContent=t("public.offline");setNotice(error.i18nKey||"public.error","error");renderSelection();}
  }
  $("publicSearchForm").addEventListener("submit",event=>{event.preventDefault();return search();});
  $("publicTask").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing){event.preventDefault();return search();}});
  $("publicTask").addEventListener("input",()=>{attempt=null;});
  $("languageAR").addEventListener("click",()=>I18n.setLanguage("ar"));$("languageEN").addEventListener("click",()=>I18n.setLanguage("en"));
  window.addEventListener("aihub:languagechange",refreshLanguage);
  $("publicReportsTab").addEventListener("click",()=>showTab("reports"));$("publicChatTab").addEventListener("click",()=>showTab("chat"));
  $("publicDiscuss").addEventListener("click",()=>showTab("chat"));$("publicCopy").addEventListener("click",copyResult);$("publicDownload").addEventListener("click",downloadResult);
  start();
})();
