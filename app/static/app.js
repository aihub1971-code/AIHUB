
(function(){
  let authHeader = "";
  let agents = [];
  let selectedAgent = null;
  let lastResponse = "";
  let latestReportId = null;
  let editingScheduleId = null;
  let archiveOffset = 0;
  let lastAttempt = null;
  let runInFlight = false;
  let sessionVersion = 0;
  const $ = id => document.getElementById(id);
  const setNotice = (id, text, kind) => {
    const el = $(id); el.textContent = text; el.className = "notice" + (kind ? " " + kind : "");
  };
  async function readJson(response){
    const text = await response.text();
    try { return text ? JSON.parse(text) : {}; } catch (_) { return {detail: text || response.statusText}; }
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
  async function fetchHealth(){
    try {
      const r = await fetch("/health", {cache:"no-store"});
      const d = await readJson(r);
      $("healthDot").className = "dot" + (r.ok ? " ok" : "");
      $("healthText").textContent = r.ok ? "الخدمة متصلة" : "تعذّر فحص الخدمة";
      $("runtimeLabel").textContent = "حالة النموذج: " + (d.stage === "ready" ? "الإعدادات موجودة" : "بانتظار الإعداد");
    } catch(_){
      $("healthText").textContent = "تعذّر الاتصال بالخدمة";
      $("runtimeLabel").textContent = "حالة النموذج: غير معروفة";
    }
  }
  function renderAgents(){
    const search = $("search").value.trim().toLowerCase();
    const shown = agents.filter(a => (a.name + " " + a.id + " " + (a.description||"")).toLowerCase().includes(search));
    $("agentList").replaceChildren();
    $("listCount").textContent = shown.length + " / " + agents.length;
    shown.forEach(a => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = "agent-item" + (selectedAgent && selectedAgent.id === a.id ? " active" : "");
      const num = document.createElement("span"); num.className = "agent-num"; num.textContent = a.number;
      const detail = document.createElement("span");
      const name = document.createElement("div"); name.className = "agent-name"; name.textContent = a.name;
      const id = document.createElement("div"); id.className = "agent-id"; id.textContent = a.id;
      detail.append(name,id); b.append(num,detail);
      b.addEventListener("click", () => chooseAgent(a.id));
      $("agentList").append(b);
    });
    $("agentCount").textContent = agents.length;
  }
  function chooseAgent(id){
    if(runInFlight) return;
    selectedAgent = agents.find(a => a.id === id);
    if(!selectedAgent) return;
    $("welcome").classList.add("hidden"); $("workArea").classList.remove("hidden");
    $("selectedNum").textContent = selectedAgent.number;
    $("selectedName").textContent = selectedAgent.name;
    $("selectedId").textContent = selectedAgent.id;
    $("selectedDescription").textContent = selectedAgent.description || "لا يوجد وصف إضافي لهذا المساعد.";
    $("selectedStatus").textContent = selectedAgent.runtime_ready ? "جاهز للتنفيذ" : "مسجل في المنصة";
    $("selectedStatus").className = "pill" + (selectedAgent.runtime_ready ? "" : " warn");
    $("skillTags").replaceChildren();
    (selectedAgent.skill_ids || []).forEach(id => {
      const tag = document.createElement("span"); tag.className = "tag"; tag.textContent = "مهارة: " + id; $("skillTags").append(tag);
    });
    $("responsePanel").classList.add("hidden"); $("runNotice").classList.add("hidden"); lastResponse = "";
    renderAgents();
  }
  function logout(){
    authHeader = ""; agents = []; selectedAgent = null; lastResponse = "";
    sessionVersion++; latestReportId = null; lastAttempt = null; archiveOffset = 0;
    $("management").classList.add("hidden"); $("accountOverview").classList.add("hidden");
    $("archiveList").replaceChildren(); $("scheduleList").replaceChildren();
    $("responseBody").replaceChildren(); $("task").value = "";
    resetScheduleForm();
    $("password").value = ""; $("username").value = "";
    $("loginBox").classList.remove("hidden"); $("sessionBox").classList.add("hidden"); $("agentSection").classList.add("hidden");
    $("workArea").classList.add("hidden"); $("welcome").classList.remove("hidden"); $("loginNotice").classList.remove("hidden");
    setNotice("loginNotice","سجّل الدخول لعرض المساعدين وتشغيل المهام.","");
  }
  $("loginBtn").addEventListener("click", async () => {
    const username = $("username").value;
    const password = $("password").value;
    if(!username || !password){ setNotice("loginNotice","أدخل اسم المستخدم وكلمة المرور.","error"); return; }
    authHeader = "Basic " + btoa(unescape(encodeURIComponent(username + ":" + password)));
    $("loginBtn").disabled = true; $("loginBtn").textContent = "جارٍ التحقق...";
    try{
      const r = await fetch("/v1/agents",{headers:{Authorization:authHeader},cache:"no-store"});
      const d = await readJson(r);
      if(!r.ok){
        authHeader = "";
        const msg = r.status === 401 ? "بيانات الدخول غير صحيحة." : r.status === 503 ? "لم تُضبط بيانات دخول مالك المنصة بعد." : "تعذّر تحميل المساعدين ("+r.status+").";
        setNotice("loginNotice",msg,"error"); return;
      }
      agents = Array.isArray(d.agents) ? d.agents : [];
      $("password").value = ""; $("loginBox").classList.add("hidden"); $("sessionBox").classList.remove("hidden");
      $("loginNotice").classList.add("hidden"); $("agentSection").classList.remove("hidden");
      renderAgents();
      sessionVersion++;
      $("management").classList.remove("hidden"); $("accountOverview").classList.remove("hidden");
      $("scheduleAgent").replaceChildren();
      agents.forEach(a=>{const option=document.createElement("option");option.value=a.id;option.textContent=a.name;$("scheduleAgent").append(option);});
      $("scheduleAgent").value = "geopolitical-morning";
      await syncDashboard();
      if(agents.length) chooseAgent(agents.find(a=>a.id==="geopolitical-morning")?.id || agents[0].id);
      else setNotice("loginNotice","لم تُرجع الواجهة أي مساعدين.","error");
    }catch(e){ authHeader = ""; setNotice("loginNotice","تعذّر الاتصال بالواجهة الخلفية. أعد المحاولة بعد قليل.","error"); }
    finally{ $("loginBtn").disabled = false; $("loginBtn").textContent = "دخول وعرض المساعدين"; }
  });
  $("password").addEventListener("keydown",e=>{if(e.key==="Enter") $("loginBtn").click();});
  $("username").addEventListener("keydown",e=>{if(e.key==="Enter") $("loginBtn").click();});
  $("logoutBtn").addEventListener("click",logout);
  $("search").addEventListener("input",renderAgents);
  $("clearBtn").addEventListener("click",()=>{lastResponse="";$("responsePanel").classList.add("hidden");$("runNotice").classList.add("hidden");});
  $("copyBtn").addEventListener("click",async()=>{
    if(!lastResponse) return;
    try{await navigator.clipboard.writeText(lastResponse);$("copyBtn").textContent="تم النسخ";setTimeout(()=>{$("copyBtn").textContent="نسخ الإجابة";},1400);}
    catch(_){setNotice("runNotice","تعذر النسخ التلقائي؛ حدّد النص وانسخه يدويًا.","error");}
  });
  $("runBtn").addEventListener("click",async()=>{
    if(!authHeader || !selectedAgent){setNotice("runNotice","سجّل الدخول واختر مساعدًا أولًا.","error");$("runNotice").classList.remove("hidden");return;}
    const task = $("task").value.trim();
    if(!task){setNotice("runNotice","اكتب المهمة قبل التنفيذ.","error");$("runNotice").classList.remove("hidden");return;}
    const requestAgentId = selectedAgent.id;
    const requestSession = sessionVersion;
    if(!lastAttempt || lastAttempt.agent !== requestAgentId || lastAttempt.task !== task){
      lastAttempt = {agent:requestAgentId, task, key:crypto.randomUUID()};
    }
    runInFlight = true;
    $("runBtn").disabled = true; $("runBtn").textContent = "جارٍ إعداد التقرير...";
    $("runHint").textContent = "يجري فحص الحدود، وجمع المصادر عند الحاجة، وحفظ النتيجة.";
    $("runNotice").classList.add("hidden"); $("responsePanel").classList.add("hidden");
    try{
      const r = await fetch("/v1/agents/"+encodeURIComponent(requestAgentId)+"/run",{
        method:"POST",headers:{"Authorization":authHeader,"Content-Type":"application/json","Idempotency-Key":lastAttempt.key},
        body:JSON.stringify({task})
      });
      const d = await readJson(r);
      if(requestSession !== sessionVersion || !authHeader) return;
      if(r.ok || d.detail?.code !== "report_in_progress") lastAttempt = null;
      if(!r.ok){
        const info = prettyError(d,r.status);
        setNotice("runNotice","تعذّر التنفيذ ("+r.status+"): "+info,"error");$("runNotice").classList.remove("hidden");return;
      }
      displayResult(d);
      $("task").value = "";
      $("runHint").textContent = "حُفظ التقرير. راجع المصادر والتواريخ والتكلفة أدناه.";
    }catch(e){if(requestSession === sessionVersion){setNotice("runNotice","انقطع الاتصال وقد يكون الطلب مستمرًا. راجع الأرشيف؛ تكرار المهمة نفسها يعيد استخدام رقم الطلب لتجنب تشغيله مرتين.","error");$("runNotice").classList.remove("hidden");}}
    finally{runInFlight=false;$("runBtn").disabled=false;$("runBtn").textContent="تشغيل المساعد";if(authHeader) await syncDashboard();}
  });

  function prettyError(data,status){
    const detail=data && data.detail;
    const code=detail && typeof detail === "object" ? detail.code : "";
    const labels={daily_report_limit:"وصلت إلى الحد اليومي للتقارير.",daily_budget_limit:"وصلت إلى ميزانية اليوم.",monthly_budget_limit:"وصلت إلى ميزانية الشهر.",pricing_not_configured:"أسعار النموذج غير مكتملة في إعدادات المنصة.",runtime_not_configured:"مفتاح تشغيل النموذج لم يُضبط بعد.",idempotency_conflict:"رقم الطلب مستخدم لمهمة مختلفة.",report_in_progress:"الطلب نفسه قيد التنفيذ؛ راجع الأرشيف بعد قليل."};
    return labels[code] || (detail && typeof detail === "object" ? detail.message || detail.code : typeof detail==="string" ? detail : null) || "تعذّر إكمال الطلب ("+status+").";
  }
  async function apiJson(path,method="GET",body){
    const headers={Authorization:authHeader};
    if(body !== undefined) headers["Content-Type"]="application/json";
    const response=await fetch(path,{method,headers,cache:"no-store",body:body === undefined ? undefined : JSON.stringify(body)});
    const data=await readJson(response);
    if(!response.ok) throw new Error(prettyError(data,response.status));
    return data;
  }
  const money=value=>Number(value || 0).toLocaleString("en-US",{style:"currency",currency:"USD",minimumFractionDigits:2,maximumFractionDigits:4});
  const localDate=value=>{const d=new Date(value);return Number.isNaN(d.getTime())?String(value || ""):d.toLocaleString("ar-AE",{timeZone:"Asia/Dubai",dateStyle:"short",timeStyle:"short"});};
  const agentName=id=>(agents.find(a=>a.id===id)||{}).name || id;
  function smallButton(label,action){const b=document.createElement("button");b.type="button";b.className="textbutton";b.textContent=label;b.addEventListener("click",action);return b;}
  function displayResult(data){
    lastResponse=data.response || "";
    latestReportId=data.report_id || null;
    renderMarkdown($("responseBody"),lastResponse || "لم تتوفر نتيجة نصية لهذا الطلب.");
    $("usage").replaceChildren();
    const u=data.usage || {}, search=data.web_search || {};
    const searchLabels={used:"تم البحث",empty:"لم تتوفر مصادر مطابقة",missing_key:"البحث غير مهيّأ",failed:"تعذّر البحث",recent_fallback:"سياق سابق للفترة",not_needed:"لم يلزم البحث",disabled_agent:"غير متاح لهذا المساعد"};
    const cells=[["النموذج",data.model],["البحث",searchLabels[search.status] || search.status],["الأخبار",data.item_count === undefined ? undefined : data.item_count+" / 6"],["رموز الإدخال",u.prompt_tokens],["رموز الإخراج",u.completion_tokens],["التكلفة التقديرية",data.accounting?.estimated_cost_usd === undefined && data.estimated_cost_usd == null ? undefined : money(data.accounting?.estimated_cost_usd ?? data.estimated_cost_usd)]];
    cells.forEach(([label,value])=>{if(value===undefined || value===null) return;const span=document.createElement("span");span.textContent=label+": "+value;$("usage").append(span);});
    $("responsePanel").classList.remove("hidden");
    $("downloadBtn").classList.toggle("hidden",!latestReportId);
    const partial=typeof data.item_count === "number" && data.item_count<6;
    setNotice("runNotice",partial?"حُفظ التقرير مع توضيح نقص الأخبار الموثّقة ضمن الفترة المطلوبة.":"حُفظ التقرير ويمكن الرجوع إليه من الأرشيف.",partial?"":"good");
  }
  async function downloadReport(id,format="md"){
    try{
      const response=await fetch("/v1/reports/"+encodeURIComponent(id)+"/download?format="+format,{headers:{Authorization:authHeader},cache:"no-store"});
      if(!response.ok) throw new Error("تعذّر تنزيل التقرير.");
      const blob=await response.blob(),url=URL.createObjectURL(blob),a=document.createElement("a");
      a.href=url;a.download="AI_HUB_Report_"+id+"."+format;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),1000);
    }catch(e){setNotice("dashboardNotice",e.message,"error");}
  }
  function renderArchive(rows){
    $("archiveList").replaceChildren();
    $("archivePageLabel").textContent=rows.length ? (archiveOffset+1)+"–"+(archiveOffset+rows.length) : "لا توجد نتائج";
    $("archivePrev").disabled=archiveOffset===0;$("archiveNext").disabled=rows.length<10;
    if(!rows.length){const p=document.createElement("p");p.className="hint";p.textContent="لا توجد تقارير في هذه الصفحة. أنشئ تقريرك الأول من مساحة المساعد.";$("archiveList").append(p);return;}
    rows.forEach(row=>{
      const article=document.createElement("article");article.className="report-row";
      const title=document.createElement("h4");title.textContent=String(row.task || "تقرير").slice(0,160);
      const meta=document.createElement("div");meta.className="report-meta";
      const state=document.createElement("span");state.className="pill"+(row.status==="failed"?" error":row.status==="pending"?" warn":"");state.textContent=({succeeded:"مكتمل",failed:"تعذّر التنفيذ",pending:"قيد التنفيذ",interrupted:"انقطع التنفيذ"})[row.status]||row.status;
      const who=document.createElement("span");who.textContent=agentName(row.agent_id)+" · "+localDate(row.created_at);
      const cost=document.createElement("p");cost.textContent=(row.trigger==="scheduled"?"مجدول":"يدوي")+" · التكلفة المحتسبة تقديريًا: "+money(row.estimated_cost_usd);
      if(row.error_code) cost.textContent+=" · "+row.error_code;
      meta.append(state,who);article.append(title,meta,cost);
      const actions=document.createElement("div");actions.className="actions";
      actions.append(smallButton("عرض",async()=>{
        try{const report=await apiJson("/v1/reports/"+encodeURIComponent(row.report_id));
          if(!report.result){setNotice("dashboardNotice",report.status==="pending"?"هذا التقرير لا يزال قيد التنفيذ.":"لا توجد نتيجة مكتملة لهذا الطلب. راجع حالة التنفيذ في السجل.","");return;}
          chooseAgent(report.agent_id);displayResult({...report.result,report_id:report.report_id,estimated_cost_usd:report.estimated_cost_usd});$("responsePanel").scrollIntoView({behavior:"smooth",block:"start"});
        }catch(e){setNotice("dashboardNotice",e.message,"error");}
      }),smallButton("تنزيل",()=>downloadReport(row.report_id)),smallButton("JSON",()=>downloadReport(row.report_id,"json")));
      article.append(actions);$("archiveList").append(article);
    });
  }
  function resetScheduleForm(){
    editingScheduleId=null;$("scheduleForm").reset();$("saveSchedule").textContent="حفظ الجدول";$("cancelScheduleEdit").classList.add("hidden");
    if(agents.some(a=>a.id==="geopolitical-morning")) $("scheduleAgent").value="geopolitical-morning";
  }
  function renderSchedules(rows,occurrences=[]){
    $("scheduleList").replaceChildren();
    if(!rows.length){const p=document.createElement("p");p.className="hint";p.textContent="لا توجد جداول محفوظة بعد.";$("scheduleList").append(p);return;}
    rows.forEach(row=>{
      const article=document.createElement("article");article.className="schedule-row";
      const title=document.createElement("h4");title.textContent=(row.name || agentName(row.agent_id))+" · "+(row.enabled?"مفعّل":"متوقف");
      const details=document.createElement("p");details.textContent=agentName(row.agent_id)+" · "+(row.times || []).join("، ")+" — دبي";
      const task=document.createElement("p");task.textContent=String(row.task).slice(0,140);
      const actions=document.createElement("div");actions.className="actions";
      const id=row.schedule_id || row.id;
      actions.append(smallButton("تعديل",()=>{
        editingScheduleId=id;$("scheduleName").value=row.name||"";$("scheduleAgent").value=row.agent_id;$("scheduleTask").value=row.task;$("scheduleTimes").value=row.times.join(", ");$("scheduleEnabled").checked=row.enabled;$("saveSchedule").textContent="حفظ التعديل";$("cancelScheduleEdit").classList.remove("hidden");$("scheduleName").focus();
      }),smallButton(row.enabled?"إيقاف":"تفعيل",async()=>{
        try{await apiJson("/v1/schedules/"+encodeURIComponent(id),"PATCH",{enabled:!row.enabled});await syncDashboard();}catch(e){setNotice("scheduleNotice",e.message,"error");}
      }),smallButton("حذف",async()=>{
        if(!window.confirm("حذف هذا الجدول؟ التقارير المحفوظة ستبقى في الأرشيف.")) return;
        try{await apiJson("/v1/schedules/"+encodeURIComponent(id),"DELETE");if(editingScheduleId===id)resetScheduleForm();await syncDashboard();}catch(e){setNotice("scheduleNotice",e.message,"error");}
      }));
      article.append(title,details,task,actions);
      const last=occurrences.find(o=>o.schedule_id===id);
      if(last){const p=document.createElement("p");const labels={succeeded:"اكتمل",failed:"تعذّر",blocked:"أوقفته حدود أو إعدادات التشغيل",skipped_missed:"فات موعده بأكثر من خمس دقائق",claimed:"قيد التنفيذ",interrupted:"انقطع التنفيذ"};p.textContent="آخر موعد: "+localDate(last.due_at)+" · "+(labels[last.status]||last.status);article.append(p);}
      $("scheduleList").append(article);
    });
  }
  async function syncDashboard(){
    if(!authHeader) return;
    const currentSession=sessionVersion;
    try{
      const [usage,system,reports,schedules,occurrences]=await Promise.all([apiJson("/v1/usage"),apiJson("/v1/system"),apiJson("/v1/reports?limit=10&offset="+archiveOffset),apiJson("/v1/schedules"),apiJson("/v1/schedule-occurrences?limit=100")]);
      if(currentSession!==sessionVersion || !authHeader) return;
      $("dailyCount").textContent=usage.daily.attempts+" / "+usage.daily.limit;$("dailyRemaining").textContent="المتبقي اليوم: "+usage.daily.remaining+" محاولة";
      $("dailyCost").textContent=money(usage.daily.estimated_cost_usd);$("dailyBudget").textContent="الميزانية: "+money(usage.daily.budget_usd);
      $("monthlyCost").textContent=money(usage.monthly.estimated_cost_usd);$("monthlyBudget").textContent="الميزانية: "+money(usage.monthly.budget_usd);
      $("providerStatus").textContent=system.model_configured?(system.provider==="openai"?"OpenAI":"Groq"):"بانتظار الإعداد";
      $("scheduleStatus").textContent="البحث: "+(system.search_configured?"مهيّأ":"غير مهيّأ")+" · الأسعار: "+(system.pricing_configured?"مهيّأة":"غير مكتملة");
      $("schedulerNotice").textContent=system.scheduler_running?"المجدول يعمل. تُشغّل الجداول المفعّلة في مواعيدها، ما دامت الحدود اليومية والميزانية تسمح.":"المجدول العام متوقف حاليًا. يمكنك حفظ الجداول، ثم تفعيل التشغيل من إعدادات الخادم بعد اختبار المفاتيح.";
      renderArchive(reports.reports || []);renderSchedules(schedules.schedules || [],occurrences.occurrences || []);
    }catch(e){if(currentSession===sessionVersion)setNotice("dashboardNotice",e.message,"error");}
  }
  $("scheduleForm").addEventListener("submit",async event=>{
    event.preventDefault();
    const times=$("scheduleTimes").value.replace(/،/g,",").split(",").map(v=>v.trim()).filter(Boolean);
    if(!times.length || times.some(v=>!/^([01]\d|2[0-3]):[0-5]\d$/.test(v))){setNotice("scheduleNotice","اكتب مواعيد صحيحة مثل 08:00, 13:00 بتوقيت دبي.","error");return;}
    const data={name:$("scheduleName").value.trim(),agent_id:$("scheduleAgent").value,task:$("scheduleTask").value.trim(),times,enabled:$("scheduleEnabled").checked};
    if(!data.task){setNotice("scheduleNotice","اكتب المهمة التي ستتكرر في هذا الجدول.","error");return;}
    $("saveSchedule").disabled=true;
    try{await apiJson(editingScheduleId?"/v1/schedules/"+encodeURIComponent(editingScheduleId):"/v1/schedules",editingScheduleId?"PATCH":"POST",data);resetScheduleForm();setNotice("scheduleNotice","حُفظ الجدول. يعتمد تنفيذه على حالة المجدول العام وحدود الاستخدام.","good");await syncDashboard();}
    catch(e){setNotice("scheduleNotice",e.message,"error");}
    finally{$("saveSchedule").disabled=false;}
  });
  $("cancelScheduleEdit").addEventListener("click",resetScheduleForm);
  $("downloadBtn").addEventListener("click",()=>latestReportId && downloadReport(latestReportId));
  $("refreshDashboard").addEventListener("click",syncDashboard);
  $("archivePrev").addEventListener("click",()=>{archiveOffset=Math.max(0,archiveOffset-10);syncDashboard();});
  $("archiveNext").addEventListener("click",()=>{archiveOffset+=10;syncDashboard();});
  $("worldNewsPreset").addEventListener("click",()=>{chooseAgent("geopolitical-morning");$("task").value="أهم 6 أخبار دولية منشورة اليوم، مع المصادر والتواريخ وسبب أهمية كل خبر.";$("task").focus();});
  $("iranNewsPreset").addEventListener("click",()=>{chooseAgent("iran-news");$("task").value="أهم 6 أخبار إيران المنشورة اليوم، مع المصادر والتواريخ وسبب أهمية كل خبر.";$("task").focus();});

  fetchHealth();
})();
