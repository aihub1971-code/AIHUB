(function(global){
  "use strict";
  const words={
    skip:["انتقل إلى المحتوى","Skip to content"],brandCaption:["البحث والمعرفة والحوار","RESEARCH & DISCUSSION"],
    agents:["المساعدون","Assistants"],mainChat:["محادثة AI HUB","AI HUB Chat"],
    eyebrow:["مساحة للبحث والحوار","A WORKSPACE FOR INQUIRY"],
    heroTitle:["افهم ما يهمّ.\nواقترب من الأدلة.","Understand what matters.\nGet closer to the evidence."],
    heroDescription:["اختر مساعدًا متخصصًا. اقرأ تقريره ومصادره، واحصل على ملف PDF، ثم ناقش ما وجدته في مكان واحد.","Choose a specialist. Read its report and sources, receive a PDF, and discuss what you discover — all in one place."],
    windowFact:["أخبار آخر 12 ساعة","12-hour news window"],pdfFact:["تقارير PDF تلقائية","Automatic report PDFs"],discussFact:["محادثة متصلة بالتقرير","Connected discussion"],
    noteTitle:["اختر. اقرأ. ناقش.","Choose. Read. Discuss."],noteDescription:["بحث عند الطلب، ومصادر واضحة، وتقارير محفوظة بوقت إعدادها. افتح أي مساعد لبدء تقرير جديد.","Research on demand. Clear sources. Reports saved as dated snapshots. Open any assistant to begin a new report."],
    count:["{n} مساعدًا متخصصًا","{n} specialist assistants"],specialists:["اختر مجال البحث","Choose your field of inquiry"],cardsHint:["اختيار المساعد يبدأ إعداد تقريره","Selecting an assistant starts its report"],
    coverageNote:["تُحدّث التقارير عند الطلب. يُعرض حتى 20 خبرًا بعد التحقق من وقت النشر وإزالة التكرار؛ وقد تقلّ النتائج بحسب الأدلة المتاحة.","Reports refresh on demand. Up to 20 news items are included after publication checks and deduplication; available evidence may yield fewer."],
    allAgents:["جميع المساعدين","All assistants"],chooseAgent:["اختر المساعد","Choose an assistant"],history:["التقارير المحفوظة","Saved reports"],
    discuss:["ناقش التقرير","Discuss report"],backToReading:["العودة للقراءة","Back to report"],
    topicLabel:["تحديد الموضوع · اختياري","Refine the topic · optional"],generate:["إنشاء تقرير حديث","Generate fresh report"],
    downloadPdf:["تنزيل PDF","Download PDF"],retryPdf:["إعادة تجهيز PDF","Retry PDF"],
    enterHint:["Enter للبدء · Shift + Enter لسطر جديد","Enter to start · Shift + Enter for a new line"],
    connectedKnowledge:["معرفتك، في محادثة واحدة","YOUR KNOWLEDGE, IN CONVERSATION"],
    mainChatTitle:["ما الذي تريد فهمه؟","What would you like to understand?"],
    mainChatDescription:["ناقش تقاريرك، قارن بين المساعدين، أو اسأل عن تطورات جديدة. تُسترجع التقارير المناسبة من هذه الجلسة تلقائيًا.","Discuss your reports, compare assistants, or ask about new developments. Relevant reports from this session are retrieved automatically."],
    footer:["راجع المصادر الأصلية للمعلومات المهمة. الأخبار والتحليلات الآلية قد تكون غير مكتملة.","Review original sources for important information. Automated reporting and analysis can be incomplete."],
    dubaiTime:["الأوقات بتوقيت دبي","Times shown in Dubai time"],
    kindBriefing:["أخبار وتحليل","NEWS & ANALYSIS"],kindReading:["قراءة تاريخية","HISTORICAL READING"],kindMoodboard:["تطوير إبداعي","CREATIVE DEVELOPMENT"],kindWorkspace:["مساعد متخصص","SPECIALIST WORKSPACE"],
    openReport:["ابدأ التقرير","Start report"],opening:["جارٍ فتح مساحة البحث…","Opening the workspace…"],
    connecting:["جارٍ الاتصال بالمنصة…","Connecting to your workspace…"],connectionError:["تعذر الاتصال. حدّث الصفحة للمحاولة مجددًا.","Connection unavailable. Refresh the page to try again."],
    noHistory:["ستظهر تقاريرك المحفوظة هنا.","Your saved reports will appear here."],
    loadingHistory:["جارٍ فتح الأرشيف…","Opening report history…"],savedVersion:["نسخة محفوظة","Saved snapshot"],latestVersion:["أحدث تقرير محفوظ","Latest saved report"],
    viewLatest:["فتح أحدث تقرير","Open latest report"],reportLanguage:["لغة التقرير","Report language"],language_ar:["العربية","Arabic"],language_en:["الإنجليزية","English"],
    researchedAt:["وقت البحث","Research cutoff"],coverage:["فترة التغطية","Coverage"],freshCount:["{n} خبرًا مستوفيًا للشروط","{n} qualifying news items"],
    referenceNote:["مراجع تاريخية أو إبداعية؛ لا تُحسب أخبارًا حديثة.","Historical or creative references; not counted as fresh news."],
    queued:["بانتظار بدء البحث","Queued for research"],searching:["جارٍ البحث","Searching"],checking_sources:["التحقق من المصادر","Checking sources"],analyzing:["التحليل","Analyzing"],preparing_pdf:["تجهيز PDF","Preparing PDF"],ready:["جاهز","Ready"],
    backgroundProgress:["يستمر إعداد التقرير حتى عند إغلاق الصفحة. يبقى التقرير السابق متاحًا بوقت إعداده الأصلي.","The report continues even if you close this page. Your previous report remains available with its original timestamp."],
    frozenCutoff:["حدّ التغطية المثبّت","Frozen research cutoff"],
    reportStarting:["مساحتك البحثية جاهزة","Your research workspace is open"],
    reportStartingNote:["يظهر التقرير هنا بعد اكتمال البحث والتحقق. يمكنك متابعة تقدم المهمة أعلاه.","Your report appears here after research and verification. Follow the job's actual progress above."],
    noReport:["ابدأ من سؤال يستحق البحث","Start with a question worth exploring"],
    noReportNote:["عدّل الموضوع إن رغبت، ثم اضغط Enter أو «إنشاء تقرير حديث».","Refine the topic if you wish, then press Enter or Generate fresh report."],
    noNews:["لا تطورات موثقة خلال آخر 12 ساعة","No verified developments in the last 12 hours"],
    noNewsNote:["لم تُرجع هذه المحاولة أخبارًا ذات وقت نشر موثّق ضمن الفترة المحددة. هذا لا يثبت عدم وقوع أحداث. راجع حدود التحقق والمصادر في ملف الإحاطة.","This attempt found no qualifying news with a verified publication timestamp inside the frozen window. This does not prove no events occurred. Review the pack's evidence limits and sources."],
    noSources:["لا مصادر قابلة للإدراج في هذه المحاولة","No eligible sources in this attempt"],
    noSourcesNote:["يعرض الملف ما أمكن إثباته وحدود الأدلة. لم تُضف مراجع لاستكمال العدد.","The pack explains what could be established and the limits of the evidence. References were not added to fill a target."],
    sourceTextNote:["يعمل هذا المساعد على النص الذي تقدمه. لا يتصل بحساباتك أو ملفاتك الخارجية.","This assistant works from the text you provide. It does not connect to your external accounts or files."],
    whyMatters:["لماذا يهمّ","Why it matters"],originalArticle:["افتح المقال الأصلي","Open original article"],eventDate:["تاريخ الحدث المنسوب إلى المصدر","Event date attributed to the source"],
    conflict_diplomacy:["الصراع والدبلوماسية","Conflict and Diplomacy"],major_consequences:["التداعيات الكبرى","Major Consequences"],
    sourceList:["المصادر والأدلة","Sources and evidence"],limitations:["حدود التحقق","Evidence limits"],background:["خلفية مرجعية","Background reference"],reference:["مرجع","Reference"],fresh:["نشر موثّق داخل الفترة","Publication verified in window"],
    undated:["وقت النشر غير موثّق","Publication time unverified"],
    visualCaption:["تكوينات بصرية أصلية باستخدام لوحة ألوان التقرير. هي تفسير إبداعي، وليست صورًا توثيقية أو صورًا مولدة بالذكاء الاصطناعي.","Original visual compositions using the report's palette. Creative interpretations, not documentary photographs or AI-generated images."],
    reportError:["تعذر إكمال هذا التقرير. بقيت النسخة السابقة محفوظة. يمكنك بدء محاولة جديدة.","This report could not be completed. Your previous snapshot is preserved. You can start a new attempt."],
    interrupted:["توقفت المهمة أثناء إعادة تشغيل الخدمة. لم يُعد البحث المدفوع تلقائيًا. ابدأ تقريرًا جديدًا للمتابعة.","The job was interrupted by a service restart. Research was not retried automatically. Start a new report to continue."],
    pdfFailed:["التقرير محفوظ ويمكن قراءته. تعذر تجهيز PDF؛ أعد تجهيز الملف دون إعادة البحث.","Your report is saved and readable. PDF creation failed; retry the PDF without researching again."],
    dailyReportLimit:["تم بلوغ حدّ التقارير لهذا اليوم. يتجدد الحد عند منتصف الليل التالي، الساعة 00:00 بتوقيت دبي (UTC+04:00). تبقى التقارير المحفوظة وملفات PDF متاحة؛ إعادة المحاولة قبل ذلك لن تبدأ بحثًا جديدًا.","Today's report limit has been reached. It resets at the next midnight, 00:00 Dubai time (UTC+04:00). Saved reports and PDFs remain available; retrying before then will not start new research."],
    dailyUsageLimit:["بلغت الخدمة حدّ الاستخدام اليومي المتاح لهذا الطلب. يتجدد الحد عند منتصف الليل التالي، الساعة 00:00 بتوقيت دبي (UTC+04:00). تبقى التقارير المحفوظة وملفات PDF متاحة.","The service's daily allowance cannot cover this request. The daily allowance resets at the next midnight, 00:00 Dubai time (UTC+04:00). Saved reports and PDFs remain available."],
    monthlyUsageLimit:["بلغت الخدمة حدّ الاستخدام الشهري المتاح لهذا الطلب. يتجدد الحد في بداية الشهر التالي بتوقيت دبي. تبقى التقارير المحفوظة وملفات PDF متاحة.","The service's monthly allowance cannot cover this request. The monthly allowance resets at the start of the next month, Dubai time. Saved reports and PDFs remain available."],
    busy:["الخدمة مشغولة أو وصلت إلى حدّ الاستخدام الحالي. جرّب لاحقًا؛ تقاريرك المحفوظة متاحة.","The service is busy or has reached its current usage limit. Try later; saved reports remain available."],
    queueFull:["لديك مهام قيد الإعداد. انتظر اكتمال إحداها قبل بدء موضوع آخر.","You have reports in progress. Wait for one to finish before starting another topic."],
    unavailable:["الخدمة غير متاحة مؤقتًا. جرّب مجددًا بعد قليل.","The service is temporarily unavailable. Try again shortly."],
    network:["تعذر تأكيد حالة الطلب. أعد الاتصال بالمحاولة نفسها لمتابعتها.","The request status could not be confirmed. Reconnect to the same attempt to continue."],
    reconnect:["متابعة المهمة","Reconnect to job"],expired:["انتهت الجلسة أو لم يعد هذا التقرير متاحًا لها. حدّث الصفحة.","This session has expired or cannot access that report. Refresh the page."],
    discussion:["ناقش مع AI HUB","Discuss with AI HUB"],newChat:["محادثة جديدة","New chat"],chatHistory:["محادثات محفوظة","Saved conversations"],
    autoContext:["التقرير المعروض ومصادره متصلان تلقائيًا.","The displayed report and its evidence are connected automatically."],
    autoAgent:["المساعد محدد. سيُسترجع تقريره المناسب تلقائيًا.","Your assistant is selected. Its relevant report is retrieved automatically."],
    autoMain:["تُسترجع التقارير المناسبة من هذه الجلسة فقط.","Relevant reports are retrieved from this session only."],
    chatWelcome:["حوّل القراءة إلى فهم أعمق","Turn reading into deeper understanding"],
    chatWelcomeNote:["اسأل عن النتائج أو أسبابها أو آثارها. لا تحتاج إلى رفع التقرير.","Ask about findings, reasoning or implications. There is no need to upload the report."],
    chatWelcomeMain:["اسأل عن مجال بحث أو قارن تقاريرك. ستظهر مصادر الإجابة ووقت تغطيتها.","Ask about a research field or compare your reports. Answers identify their evidence and research cutoff."],
    suggestionSummary:["لخّص هذا التقرير.","Summarize this report."],suggestionUae:["ما أهم الآثار على الإمارات؟","What are the most important implications for the UAE?"],
    suggestionWhy:["لماذا توصلت إلى هذه الاستنتاجات؟","Why did you reach these conclusions?"],
    suggestionSince:["ما الذي حدث منذ هذا التقرير؟","What has happened since this briefing?"],
    suggestionCompare:["قارن تقييم إيران بالتقييم الجيوسياسي العالمي.","Compare the Iran and global geopolitical assessments."],
    suggestionLatest:["ما أحدث التطورات المتعلقة بإيران؟","What are the latest developments related to Iran?"],
    suggestionEducation:["ما الجديد في التعليم العالمي؟","What is new in global education?"],
    chatPlaceholder:["اسأل عن التقرير أو موضوع جديد…","Ask about the report or a new topic…"],
    chatInput:["رسالتك إلى AI HUB","Your message to AI HUB"],send:["إرسال","Send"],you:["أنت","You"],
    chatThinking:["يجري إعداد إجابة من الأدلة المتاحة…","Preparing an answer from the available evidence…"],
    chatResearch:["يجري بحث جديد للإجابة عن المستجدات…","Researching fresh evidence for your question…"],
    chatKeyboard:["Enter للإرسال · Shift + Enter لسطر جديد","Enter to send · Shift + Enter for a new line"],
    chatError:["تعذر إكمال الإجابة. الرسائل المحفوظة متاحة؛ تحقق من المحاولة قبل إعادة إرسالها.","The answer could not be completed. Saved messages remain available; check this attempt before sending it again."],
    retryMessage:["تحقق من المحاولة","Check this attempt"],chatEvidence:["استندت الإجابة إلى {n} تقريرًا محفوظًا.","This answer used {n} saved report snapshots."],
    freshEvidence:["بحث جديد حتى","Fresh research through"],viewUpdated:["فتح التقرير المحدّث","Open updated report"],
    savedMessages:["رسائل المحادثة المحفوظة","Saved conversation messages"],loadEarlier:["رسائل أقدم","Earlier messages"],
    chatLoading:["جارٍ استعادة المحادثة…","Restoring the conversation…"],sourceMismatch:["تعذر اعتماد مصادر الإجابة. لم تُعرض إجابة غير موثّقة. يمكنك طرح السؤال مجددًا.","The answer's sources could not be validated. No unverified answer was displayed. You can ask again."]
  };
  const icons={"ubi-research":"◉","iran-news":"⌁","uae-eu-trade-talks":"⇄","geopolitical-morning":"◎","education-observatory":"▤","historical-reading-companion":"◷","story-moodboard":"◇","economic-cultural-briefing":"↗","decision-briefing":"⑂","mom-agent":"≡","whatsapp-briefing":"▱","literary-translation":"A","pre-meeting-briefing":"⊞"};
  function activeJob(job){return Boolean(job && ["queued","running"].includes(job.status));}
  function canAcceptReport(state,report){return Boolean(report && Number.isSafeInteger(report.generation_seq) && report.generation_seq >= (state.latest?.generation_seq || 0));}
  function sameRequest(job,topic,language){return activeJob(job)&&job.topic===topic.trim()&&job.language===language;}
  const core={activeJob,canAcceptReport,sameRequest};
  global.AIHubWorkspaceCore=core;
  if(typeof module!=="undefined"&&module.exports)module.exports=core;
  if(!global.document)return;
  const $=id=>document.getElementById(id),el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=String(text);return n;};
  const markdown=global.AIHubMarkdown;
  let lang="ar",ready=false,agents=[],selectedId=null,view="home",navigation=0,mobileChat=false;
  const states=new Map(),chatStates=new Map(),polls=new Map();
  try{const saved=localStorage.getItem("aihub.language");if(saved==="ar"||saved==="en")lang=saved;}catch(_){}
  const T=(key,params={})=>{let value=words[key]?.[lang==="en"?1:0]||key;for(const [name,text]of Object.entries(params))value=value.replaceAll("{"+name+"}",String(text));return value;};
  const nameOf=a=>a?.[lang==="ar"?"name_ar":"name_en"]||a?.name||"AI HUB";
  const titleOf=r=>r?.result?.title||r?.title||r?.topic||"AI HUB";
  const uuid=()=>global.crypto?.randomUUID?.()||String(Date.now())+"-"+Math.random().toString(16).slice(2);
  const kindKey=a=>a?.kind==="reading"?"kindReading":a?.kind==="moodboard"?"kindMoodboard":a?.kind==="workspace"?"kindWorkspace":"kindBriefing";
  function moment(value){if(!value)return "";const date=new Date(value);if(!Number.isFinite(date.getTime()))return "";return date.toLocaleString(lang==="ar"?"ar-AE":"en-GB",{timeZone:"Asia/Dubai",dateStyle:"medium",timeStyle:"short"});}
  function state(id){if(!states.has(id))states.set(id,{topic:"",report:null,latest:null,job:null,tab:"evidence",pinned:false,submitting:false,attempt:null,notice:null,history:[],loadEpoch:0});return states.get(id);}
  function profile(id){return agents.find(agent=>agent.id===id);}
  function current(){return selectedId?state(selectedId):null;}
  function defaultTopic(id){return profile(id)?.["default_topic_"+lang]||"";}
  function saveNavigation(){try{if(view==="workspace"&&selectedId)sessionStorage.setItem("aihub.workspace.agent",selectedId);else sessionStorage.removeItem("aihub.workspace.agent");}catch(_){}}
  function showNotice(node,key){node.hidden=!key;node.textContent=key?T(key):"";}
  function errorKey(error){
    if(error?.code==="daily_report_limit")return "dailyReportLimit";
    if(error?.code==="daily_budget_limit")return "dailyUsageLimit";
    if(error?.code==="monthly_budget_limit")return "monthlyUsageLimit";
    if(error?.jobStatus==="interrupted")return "interrupted";
    if(error?.jobStatus==="pdf_failed")return "pdfFailed";
    if(error?.code==="chat_source_mismatch")return "sourceMismatch";
    if(error?.status===401||error?.status===404)return "expired";
    if(["report_queue_full","report_queue_session_limit","owner_job_limit","report_job_limit","owner_queue_full"].includes(error?.code))return "queueFull";
    if(error?.status===429)return "busy";if(error?.status===503)return "unavailable";
    return error?.network?"network":"reportError";
  }
  function jobFailure(job){return {status:502,code:job?.error_code||"report_generation_failed",jobStatus:job?.status};}
  async function request(path,{method="GET",body,key}={}){
    if(!path.startsWith("/v1/public/")||/[\u0000-\u0020\u007f\\]/.test(path))throw new Error("Invalid public request");
    const headers={};if(body!==undefined)headers["Content-Type"]="application/json";if(key)headers["Idempotency-Key"]=key;
    const controller=new AbortController(),timeout=setTimeout(()=>controller.abort(),110000);
    try{
      const response=await fetch(path,{method,headers,body:body===undefined?undefined:JSON.stringify(body),credentials:"same-origin",cache:"no-store",redirect:"error",signal:controller.signal});
      let data;try{data=await response.json();}catch(_){throw {status:502,code:"invalid_response"};}
      if(!response.ok)throw {status:response.status,code:data?.detail?.code||"request_failed"};
      return data;
    }catch(error){if(error?.status)throw error;throw {network:true,code:"network_error"};}finally{clearTimeout(timeout);}
  }
  function button(text,cls,handler){const node=el("button",cls,text);node.type="button";node.addEventListener("click",handler);return node;}
  function link(text,url,cls){const href=markdown.safeLink(url);if(!href)return el("span",cls,text);const node=el("a",cls,text);node.href=href;node.target="_blank";node.rel="noopener noreferrer";return node;}
  function setLanguage(value){
    if(!["ar","en"].includes(value))return;
    const previous=lang;lang=value;try{localStorage.setItem("aihub.language",lang);}catch(_){}
    for(const [id,s]of states){if(!s.topic||s.topic===profile(id)?.["default_topic_"+previous])s.topic=defaultTopic(id);}
    document.documentElement.lang=lang;document.documentElement.dir=lang==="ar"?"rtl":"ltr";
    document.querySelectorAll("[data-text]").forEach(node=>{node.textContent=T(node.dataset.text);});
    $("language-ar").setAttribute("aria-pressed",String(lang==="ar"));$("language-en").setAttribute("aria-pressed",String(lang==="en"));
    $("agent-count").textContent=T("count",{n:agents.length});
    renderCards();renderSelector();renderWorkspace();renderVisibleChat();renderHistory();
  }
  function renderCards(){
    $("agent-grid").replaceChildren();
    for(const [index,a]of agents.entries()){
      const card=button("","agent-card"+(index>7?" secondary-agent":""),()=>openAgent(a.id,true));
      card.dataset.agentId=a.id;card.disabled=!ready;card.setAttribute("aria-label",nameOf(a)+" — "+T("openReport"));
      const top=el("span","agent-card-top");top.append(el("span","agent-icon",icons[a.id]||"◉"),el("span","agent-number",String(index+1).padStart(2,"0")));
      const bottom=el("span","agent-card-bottom");bottom.append(el("span","",T(kindKey(a))),el("span","card-arrow",lang==="ar"?"←":"→"));
      card.append(top,el("h3","",nameOf(a)),el("p","",a["default_topic_"+lang]),bottom);
      $("agent-grid").append(card);
    }
  }
  function renderSelector(){
    const selector=$("agent-selector");selector.replaceChildren();
    for(const a of agents){const option=el("option","",nameOf(a));option.value=a.id;selector.append(option);}
    selector.value=selectedId||agents[0]?.id||"";
  }
  function setView(value){
    view=value;navigation++;mobileChat=false;
    $("home-view").hidden=value!=="home";$("workspace-view").hidden=value!=="workspace";$("main-chat-view").hidden=value!=="chat";
    $("workspace-view").classList.remove("chat-open");
    $("nav-home").classList.toggle("is-current",value!=="chat");$("nav-chat").classList.toggle("is-current",value==="chat");
    saveNavigation();renderVisibleChat();
  }
  async function openAgent(id,generate){
    if(!ready||!profile(id))return;
    selectedId=id;const s=state(id);if(generate)s.topic=defaultTopic(id);else if(!s.topic)s.topic=defaultTopic(id);
    setView("workspace");renderSelector();renderWorkspace();
    const epoch=++s.loadEpoch;
    try{
      const data=await request("/v1/public/workspace/agents/"+encodeURIComponent(id));
      if(epoch!==s.loadEpoch)return;
      if(data.last_successful_report)acceptReport(id,data.last_successful_report);
      if(data.active_job)acceptJob(id,data.active_job);
      if(generate)await generateReport(id);
      else if(activeJob(s.job))pollJob(id,s.job.job_id);
    }catch(error){s.notice=errorKey(error);if(selectedId===id)renderWorkspace();}
  }
  function acceptReport(id,record){
    const s=state(id);if(!canAcceptReport(s,record))return;
    s.latest=record;
    if(!s.pinned && (!s.report || record.generation_seq>=s.report.generation_seq))s.report=record;
    if(selectedId===id&&view==="workspace"){renderWorkspace();renderVisibleChat();}
  }
  function acceptJob(id,job){
    const s=state(id);if(!job||job.generation_seq<(s.job?.generation_seq||0))return;
    s.job=job;if(selectedId===id&&view==="workspace")renderProgress();
  }
  async function generateReport(id=selectedId){
    if(!ready||!id)return;const s=state(id);
    if(s.submitting)return;
    const topic=(s.topic||defaultTopic(id)).trim(),language=lang;
    if(sameRequest(s.job,topic,language)){s.pinned=false;if(s.latest)s.report=s.latest;renderWorkspace();pollJob(id,s.job.job_id);return;}
    if(!s.attempt||s.attempt.topic!==topic||s.attempt.language!==language)s.attempt={topic,language,key:uuid()};
    const attempt=s.attempt;s.submitting=true;s.notice=null;s.pinned=false;
    if(s.latest)s.report=s.latest;if(selectedId===id)renderWorkspace();
    try{
      const data=await request("/v1/public/workspace/agents/"+encodeURIComponent(id)+"/reports",{method:"POST",body:{topic:attempt.topic,language:attempt.language},key:attempt.key});
      acceptJob(id,data.job);s.attempt=null;pollJob(id,data.job.job_id);
    }catch(error){s.notice=errorKey(error);if(!error.network)s.attempt=null;}finally{s.submitting=false;if(selectedId===id)renderWorkspace();}
  }
  const pause=ms=>new Promise(resolve=>setTimeout(resolve,ms));
  function pollJob(id,jobId){
    if(polls.has(jobId))return polls.get(jobId);
    const promise=(async()=>{
      let errors=0;const began=Date.now();
      while(Date.now()-began<30*60*1000){
        try{
          const data=await request("/v1/public/workspace/jobs/"+encodeURIComponent(jobId));errors=0;
          acceptJob(id,data.job);if(data.report)acceptReport(id,data.report);
          const s=state(id);
          if(["failed","interrupted","pdf_failed"].includes(data.job.status)&&s.job?.job_id===jobId){s.notice=errorKey(jobFailure(data.job));if(selectedId===id)renderWorkspace();}
          if(!activeJob(data.job))return data;
        }catch(error){if(++errors>=4){const s=state(id);s.notice=errorKey(error);if(selectedId===id)renderWorkspace();return null;}}
        await pause(1800);
      }
      const s=state(id);s.notice="network";if(selectedId===id)renderWorkspace();return null;
    })().finally(()=>polls.delete(jobId));
    polls.set(jobId,promise);return promise;
  }
  function renderProgress(){
    const s=current(),target=$("job-progress");target.replaceChildren();
    if(!s||!activeJob(s.job)){target.hidden=true;return;}
    target.hidden=false;const stage=s.job.stage==="queued"?"queued":s.job.stage;
    target.append(el("p","job-title",T(stage)),el("p","job-note",T("backgroundProgress")));
    const cutoff=s.job.research_window?.end;if(cutoff)target.append(el("p","job-note",T("frozenCutoff")+": "+moment(cutoff)));
    const list=el("ol","stage-list"),stages=["searching","checking_sources","analyzing","preparing_pdf","ready"],index=stages.indexOf(stage);
    stages.forEach((name,i)=>list.append(el("li",i<index?"done":i===index?"current":"",T(name))));target.append(list);
  }
  function empty(target,title,note){const box=el("div","empty-report");box.append(el("span","empty-glyph","▤"),el("h3","",T(title)),el("p","",T(note)));target.append(box);}
  function renderWorkspace(){
    if(!selectedId||!profile(selectedId))return;
    const s=current(),a=profile(selectedId);
    $("workspace-heading").textContent=nameOf(a);$("workspace-kicker").textContent=T(kindKey(a));
    if(document.activeElement!==$("topic-input"))$("topic-input").value=s.topic||defaultTopic(selectedId);
    $("generate-button").disabled=!ready||s.submitting;
    const views=a.views||[{id:"evidence",title_ar:"الأخبار",title_en:"News"},{id:"pack",title_ar:"ملف الإحاطة",title_en:"Briefing Pack"}];
    views.forEach(v=>{const tab=$("tab-"+v.id);tab.textContent=v["title_"+lang];tab.setAttribute("aria-selected",String(s.tab===v.id));tab.tabIndex=s.tab===v.id?0:-1;});
    const pdf=$("pdf-download"),retry=$("pdf-retry");pdf.hidden=s.report?.pdf?.status!=="ready";retry.hidden=s.report?.pdf?.status!=="failed";
    if(!pdf.hidden){pdf.href="/v1/public/workspace/reports/"+encodeURIComponent(s.report.report_id)+"/pdf";pdf.setAttribute("download","AI_HUB_Report.pdf");}
    else pdf.removeAttribute("href");
    showNotice($("report-notice"),s.notice);
    if(s.notice==="network"&&activeJob(s.job))$("report-notice").append(button(T("reconnect"),"quiet-button",()=>{s.notice=null;pollJob(selectedId,s.job.job_id);renderWorkspace();}));
    renderProgress();renderReport();$("mobile-chat-toggle").textContent=T(mobileChat?"backToReading":"discuss");$("mobile-chat-toggle").setAttribute("aria-expanded",String(mobileChat));
  }
  function renderReport(){
    const s=current(),target=$("report-content"),meta=$("report-meta");target.replaceChildren();meta.replaceChildren();
    if(!s?.report){meta.hidden=true;target.dir=lang==="ar"?"rtl":"ltr";target.lang=lang;empty(target,activeJob(s?.job)||s?.submitting?"reportStarting":"noReport",activeJob(s?.job)||s?.submitting?"reportStartingNote":"noReportNote");return;}
    meta.hidden=false;const r=s.report,value=r.result||{},window=value.research_window||r.research_window||{};
    const stamp=el("div","report-stamp");stamp.append(el("span","snapshot-badge"+(s.pinned?" saved-badge":""),T(s.pinned?"savedVersion":"latestVersion")));
    stamp.append(el("span","",T("reportLanguage")+": "+T("language_"+(value.language||r.language||"ar"))));
    meta.append(stamp);
    const title=el("h2","",titleOf(r));title.dir=value.language==="ar"?"rtl":"ltr";title.lang=value.language||r.language;meta.append(title);
    if(window.end)meta.append(el("p","report-window",T("researchedAt")+": "+moment(window.end)+" · UTC+04:00"));
    if(window.start&&window.end)meta.append(el("p","report-window",T("coverage")+": "+moment(window.start)+" — "+moment(window.end)));
    const note=["reading","moodboard"].includes(value.kind)?T("referenceNote"):value.kind==="workspace"?T("sourceTextNote"):T("freshCount",{n:value.items?.length||0});
    meta.append(el("p","report-window",note));
    if(s.pinned&&s.latest?.report_id!==r.report_id)meta.append(button(T("viewLatest"),"quiet-button",()=>{s.pinned=false;s.report=s.latest;renderWorkspace();renderVisibleChat();}));
    target.dir=value.language==="ar"?"rtl":"ltr";target.lang=value.language||r.language;
    target.setAttribute("aria-labelledby","tab-"+s.tab);
    if(s.tab==="pack"){renderPack(target,value);return;}
    if(value.kind==="workspace"){const box=el("div","pack-section");box.append(el("p","news-summary",value.topic||r.topic),el("p","visual-caption",T("sourceTextNote")));target.append(box);return;}
    if(["reading","moodboard"].includes(value.kind)){renderSources(target,value.sources||[],false);if(!(value.sources||[]).length)empty(target,"noSources","noSourcesNote");return;}
    const items=value.items||[];
    if(!items.length){empty(target,"noNews","noNewsNote");renderLimitations(target,value.limitations);return;}
    if(value.agent_id==="iran-news"){
      for(const section of ["conflict_diplomacy","major_consequences"]){
        const chosen=items.filter(item=>section==="conflict_diplomacy"?item.section!=="major_consequences":item.section==="major_consequences");
        if(chosen.length){target.append(el("h3","evidence-heading",T(section)));chosen.forEach(item=>renderArticle(target,item,value));}
      }
    }else items.forEach(item=>renderArticle(target,item,value));
    renderLimitations(target,value.limitations);
  }
  function citations(target,ids,value){
    const row=el("div","citation-row"),sources=value.sources||[];
    for(const id of [...new Set(ids||[])]){
      const index=sources.findIndex(s=>s.id===id),source=sources[index];
      if(source?.url)row.append(link("["+String(index+1)+"] "+(source.publisher||source.title||""),source.url,"citation-link"));
    }
    if(row.childNodes.length)target.append(row);
  }
  function renderArticle(target,item,value){
    const article=el("section","news-item"),top=el("div","article-topline");
    top.append(el("span","publisher-name",item.publisher||""),el("time","",moment(item.published_at||item.published_date)));
    if(item.source_kind)top.append(el("span","source-role",item.source_kind==="primary"?(lang==="ar"?"مصدر أولي":"Primary source"):(lang==="ar"?"تقرير صحفي":"Reporting")));
    const headline=el("h3");headline.append(link(item.headline||"",item.url,""));
    article.append(top,headline,el("p","news-summary",item.summary||""));
    if(item.why_it_matters){const why=el("div","why-matters");why.append(el("strong","",T("whyMatters")),document.createTextNode(item.why_it_matters));article.append(why);}
    article.append(link(T("originalArticle")+" ↗",item.url,"article-link"));
    if(item.event_date)article.append(el("p","event-date",T("eventDate")+": "+item.event_date));
    citations(article,item.source_ids,value);target.append(article);
  }
  function renderPack(target,value){
    if(value.kind==="moodboard")renderVisuals(target,value);
    const sections=value.sections||[];
    if(sections.length){
      for(const section of sections){const block=el("section","pack-section");block.append(el("h3","",section.title||""));const body=el("div","markdown");markdown.render(body,section.body||"");block.append(body);citations(block,section.source_ids,value);target.append(block);}
    }else{const body=el("div","markdown");markdown.render(body,value.response||"");target.append(body);}
    renderLimitations(target,value.limitations);renderSources(target,value.sources||[],true);
  }
  function renderSources(target,sources,heading){
    const section=el("section","sources-section");if(heading&&sources.length)section.append(el("h3","",T("sourceList")));
    for(const [index,source]of sources.entries()){const card=el("div","source-record");card.append(link("["+String(index+1)+"] "+(source.title||source.publisher||""),source.url,""));
      const role=source.role==="fresh"?T("fresh"):source.role==="background"?T("background"):T("reference");
      card.append(el("small","",[source.publisher,source.published_at?moment(source.published_at):T("undated"),role].filter(Boolean).join(" · ")));
      if(!heading&&source.excerpt)card.append(el("p","news-summary",source.excerpt));section.append(card);}
    target.append(section);
  }
  function renderLimitations(target,items){
    if(!Array.isArray(items)||!items.length)return;
    const details=el("details","limitations");details.append(el("summary","",T("limitations")));
    for(const item of items)if(typeof item==="string")details.append(el("p","",item));target.append(details);
  }
  function renderVisuals(target,value){
    const palette=(value.visuals?.palette||[]).filter(c=>/^#[0-9a-f]{6}$/i.test(c.hex)).slice(0,8);if(palette.length<2)return;
    const block=el("section","mood-visuals"),composition=el("div","visual-composition");composition.setAttribute("aria-hidden","true");
    [0,1].forEach(index=>{const panel=el("div","visual-panel");panel.style.backgroundColor=palette[index].hex;
      for(const [j,cls]of ["sun","horizon","structure"].entries()){const shape=el("i",cls);shape.style.backgroundColor=palette[(index+j+1)%palette.length].hex;panel.append(shape);}composition.append(panel);});
    const swatches=el("div","palette");for(const color of palette){const swatch=el("div","palette-swatch"),chip=el("div","palette-chip");chip.style.backgroundColor=color.hex;swatch.append(chip,el("span","",color.name||color.hex),el("span","",color.hex.toUpperCase()));swatches.append(swatch);}
    block.append(composition,swatches,el("p","visual-caption",T("visualCaption")));target.append(block);
  }
  async function loadHistory(){
    const id=selectedId;if(!id)return;
    $("report-history").textContent=T("loadingHistory");
    try{const data=await request("/v1/public/workspace/reports?agent_id="+encodeURIComponent(id));state(id).history=data.reports||[];if(selectedId===id)renderHistory();}
    catch(error){if(selectedId===id)$("report-history").textContent=T(errorKey(error));}
  }
  function renderHistory(){
    if(!selectedId)return;const container=$("report-history");container.replaceChildren();const list=current().history;
    if(!list.length){container.append(el("p","",T("noHistory")));return;}
    for(const record of list){const row=button("","history-record",()=>openSaved(record.report_id,record.agent_id));row.append(el("span","",record.title||record.topic),el("small","",moment(record.research_cutoff||record.research_window?.end||record.created_at)+" · "+T("language_"+record.language)));container.append(row);}
  }
  async function openSaved(reportId,agentId){
    const epoch=++navigation;
    try{
      const record=await request("/v1/public/workspace/reports/"+encodeURIComponent(reportId));if(epoch!==navigation)return;
      const id=record.agent_id||agentId;if(!profile(id))return;selectedId=id;const s=state(id);acceptReport(id,record);s.report=record;s.pinned=true;s.topic=record.topic;s.notice=null;
      setView("workspace");renderSelector();renderWorkspace();renderVisibleChat();$("history-menu").open=false;
    }catch(error){showNotice($("global-notice"),errorKey(error));}
  }
  async function retryPdf(){
    const s=current(),id=selectedId,reportId=s?.report?.report_id;if(!reportId)return;
    $("pdf-retry").disabled=true;
    try{const data=await request("/v1/public/workspace/reports/"+encodeURIComponent(reportId)+"/pdf/retry",{method:"POST",body:{}});s.notice=null;
      if(data.job?.job_id){const result=await pollJob(id,data.job.job_id);if(result?.report&&s.report?.report_id===reportId){s.report=result.report;if(s.latest?.report_id===reportId)s.latest=result.report;}}
      if(selectedId===id)renderWorkspace();
    }catch(error){s.notice=errorKey(error);if(selectedId===id)renderWorkspace();}finally{$("pdf-retry").disabled=false;}
  }
  function chatKey(){return view==="chat"?"main":selectedId;}
  function validConversationId(value){return typeof value==="string"&&/^conv_[a-f0-9]{32}$/.test(value);}
  function chatState(key){
    if(!chatStates.has(key)){let id=null;try{id=sessionStorage.getItem("aihub.workspace.chat."+key);}catch(_){}
      if(id&&!validConversationId(id)){id=null;try{sessionStorage.removeItem("aihub.workspace.chat."+key);}catch(_){}}
      chatStates.set(key,{key,id,messages:[],draft:"",sending:false,loading:false,loaded:false,error:null,status:null,attempt:null,context:null,history:[],before:null});}
    return chatStates.get(key);
  }
  function saveConversation(c){try{if(validConversationId(c.id))sessionStorage.setItem("aihub.workspace.chat."+c.key,c.id);else sessionStorage.removeItem("aihub.workspace.chat."+c.key);}catch(_){}}
  function visibleChat(c){return (view==="workspace"||view==="chat")&&chatKey()===c.key;}
  function chatRoot(){return view==="chat"?$("main-discussion-panel"):$("discussion-panel");}
  function renderVisibleChat(){if(view==="home"||!chatKey())return;const c=chatState(chatKey());renderChat(c);if(!c.loaded&&!c.loading)loadConversation(c);}
  async function loadConversation(c,earlier=false){
    if(c.loading)return;c.loading=true;if(visibleChat(c))renderChat(c);
    try{
      if(c.id&&!validConversationId(c.id)){c.id=null;saveConversation(c);}
      if(c.id){
        const data=await request("/v1/public/chat/conversations/"+encodeURIComponent(c.id)+(earlier&&c.before?"?before="+c.before:""));
        c.messages=earlier?[...(data.messages||[]),...c.messages]:(data.messages||[]);c.before=data.pagination?.has_more?data.pagination.next_before:null;
      }
      const data=await request("/v1/public/chat/conversations?limit=50");c.history=data.conversations||[];c.loaded=true;c.error=null;
    }catch(error){c.error=errorKey(error);if(error.status===404){c.id=null;c.messages=[];c.before=null;saveConversation(c);c.loaded=true;}}
    finally{c.loading=false;if(visibleChat(c))renderChat(c);}
  }
  function renderChat(c){
    if(!visibleChat(c))return;const root=chatRoot(),oldInput=root.querySelector("textarea");if(oldInput)c.draft=oldInput.value;
    const hadFocus=document.activeElement===oldInput,selection=hadFocus?oldInput.selectionStart:null;
    const oldScroll=root.querySelector(".chat-scroll"),atBottom=!oldScroll||oldScroll.scrollHeight-oldScroll.scrollTop-oldScroll.clientHeight<90,scrollTop=oldScroll?.scrollTop||0;
    const box=el("div","chat-box"),head=el("header","chat-header"),row=el("div","chat-heading-row");
    const heading=el("h2","",T("discussion"));heading.id=view==="chat"?"main-discussion-heading":"discussion-heading";
    row.append(heading,button(T("newChat"),"quiet-button",()=>{if(c.sending)return;c.id=null;c.messages=[];c.context=null;c.attempt=null;c.error=null;c.draft="";saveConversation(c);renderChat(c);}));
    const context=el("p","chat-context");context.append(el("span","context-indicator"),document.createTextNode(T(c.key==="main"?"autoMain":state(c.key).report?"autoContext":"autoAgent")));
    head.append(row,context);
    if(c.history.length){const select=el("select","chat-history-select");select.setAttribute("aria-label",T("chatHistory"));
      const placeholder=el("option","",T("chatHistory"));placeholder.setAttribute("value","");placeholder.disabled=true;placeholder.selected=true;select.append(placeholder);
      for(const item of c.history){if(!validConversationId(item.conversation_id))continue;const option=el("option","",item.title||moment(item.created_at));option.value=item.conversation_id;select.append(option);}select.value="";
      select.disabled=c.sending||c.loading;select.addEventListener("change",()=>{if(c.sending||c.loading||!validConversationId(select.value)||!c.history.some(item=>item.conversation_id===select.value))return;c.id=select.value;c.messages=[];c.context=null;c.draft="";c.attempt=null;saveConversation(c);loadConversation(c);});head.append(select);}
    const scroll=el("div","chat-scroll");scroll.setAttribute("role","log");scroll.setAttribute("aria-label",T("savedMessages"));
    if(c.before)scroll.append(button(T("loadEarlier"),"quiet-button",()=>loadConversation(c,true)));
    if(!c.messages.length&&!c.sending){const welcome=el("div","chat-welcome");welcome.append(el("h3","",T("chatWelcome")),el("p","",T(c.key==="main"?"chatWelcomeMain":"chatWelcomeNote")));
      const suggestions=el("div","chat-suggestions"),keys=c.key==="main"?["suggestionLatest","suggestionCompare","suggestionEducation","suggestionUae"]:["suggestionSummary","suggestionUae","suggestionWhy","suggestionSince"];
      for(const key of keys)suggestions.append(button(T(key),"chat-suggestion",()=>{c.draft=T(key);const input=chatRoot().querySelector("textarea");input.value=c.draft;input.focus();}));welcome.append(suggestions);scroll.append(welcome);}
    for(const message of c.messages){
      const block=el("div","chat-message "+message.role);block.append(el("div","chat-message-label",message.role==="user"?T("you"):"AI HUB"));
      const body=el("div","message-body"+(message.role==="assistant"?" markdown":""));body.dir="auto";
      if(message.role==="assistant")markdown.render(body,message.content||"");else body.textContent=message.content||"";
      block.append(body);if(message.status==="failed")block.append(el("p","chat-error",T("chatError")));scroll.append(block);
    }
    if(c.context?.report_ids?.length){const note=el("div","chat-evidence-note");note.append(el("p","",T("chatEvidence",{n:c.context.report_ids.length})));
      for(const cutoff of c.context.research_cutoffs||[])note.append(el("div","",nameOf(profile(cutoff.agent_id))+": "+moment(cutoff.cutoff)));
      if(c.context.fresh_research&&c.context.report_id)note.append(button(T("viewUpdated"),"quiet-button",()=>openSaved(c.context.report_id,c.context.agent_ids?.[0])));scroll.append(note);}
    const composer=el("div","chat-composer-area");
    if(c.loading)composer.append(el("p","chat-status",T("chatLoading")));
    if(c.status){const status=el("p","chat-status",T(c.status));status.setAttribute("role","status");composer.append(status);}
    if(c.error){const error=el("div","chat-error",T(c.error));error.setAttribute("role","alert");
      if(c.attempt&&!c.sending)error.append(button(T("retryMessage"),"quiet-button",()=>sendChat(c,true)));composer.append(error);}
    const form=el("form","chat-form"),input=el("textarea");input.rows=2;input.maxLength=8000;input.dir="auto";input.placeholder=T("chatPlaceholder");input.setAttribute("aria-label",T("chatInput"));input.value=c.draft;input.disabled=c.sending||c.loading;
    input.addEventListener("input",()=>{c.draft=input.value;});
    input.addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing){event.preventDefault();form.requestSubmit();}});
    const bar=el("div","chat-composer-bar"),send=el("button","chat-send",T("send"));send.type="submit";send.disabled=!ready||c.sending||c.loading;
    bar.append(el("span","",T("chatKeyboard")),send);form.append(input,bar);form.addEventListener("submit",event=>{event.preventDefault();sendChat(c);});
    composer.append(form);box.append(head,scroll,composer);root.replaceChildren(box);
    if(atBottom)scroll.scrollTop=scroll.scrollHeight;else scroll.scrollTop=scrollTop;
    if(hadFocus&&!input.disabled){input.focus();if(selection!==null)input.setSelectionRange(selection,selection);}
  }
  async function sendChat(c,retry=false){
    if(!ready||c.sending||c.loading)return;
    const text=c.draft.trim();if(!retry&&!text)return;
    if(!retry){
      const currentReport=c.key==="main"?null:state(c.key).report;
      c.attempt={key:uuid(),body:{message:text,language:lang,agent_id:c.key==="main"?null:c.key,report_id:currentReport?.report_id||null,topic:c.key==="main"?"":state(c.key).topic}};
    }
    const attempt=c.attempt;if(!attempt)return;
    c.sending=true;c.error=null;c.status="chatThinking";
    if(visibleChat(c))renderChat(c);
    try{
      if(!validConversationId(c.id)){const conversation=await request("/v1/public/chat/conversations",{method:"POST",body:{language:attempt.body.language,title:c.key==="main"?"AI HUB":nameOf(profile(c.key))}});if(!validConversationId(conversation.conversation_id))throw {status:502,code:"invalid_response"};c.id=conversation.conversation_id;saveConversation(c);}
      let answer;
      for(;;){
        answer=await request("/v1/public/workspace/chat/conversations/"+encodeURIComponent(c.id)+"/messages",{method:"POST",body:attempt.body,key:attempt.key});
        if(answer.status!=="researching")break;
        c.status="chatResearch";attempt.body.fresh_job_id=answer.job.job_id;
        if(answer.agent_id){acceptJob(answer.agent_id,answer.job);pollJob(answer.agent_id,answer.job.job_id);}
        if(visibleChat(c))renderChat(c);
        // Observe a saved structured report before asking for its answer. PDF
        // progress is independent, so a PDF failure cannot block discussion.
        let saved=false;
        while(!saved){
          await pause(1800);
          const update=await request("/v1/public/workspace/jobs/"+encodeURIComponent(answer.job.job_id));
          if(update.report){acceptReport(answer.agent_id,update.report);saved=true;}
          else if(["failed","interrupted"].includes(update.job.status))throw jobFailure(update.job);
        }
        c.status="chatThinking";if(visibleChat(c))renderChat(c);
      }
      const seen=new Set(c.messages.map(message=>message.id));
      for(const message of [answer.user_message,answer.message])if(message&&!seen.has(message.id))c.messages.push(message);
      c.context=answer.context;c.draft="";c.attempt=null;
      if(answer.conversation)c.history=[answer.conversation,...c.history.filter(row=>row.conversation_id!==c.id)];
    }catch(error){c.error=error?.network?"network":errorKey(error);if(!error?.network&&error?.code!=="chat_in_progress"&&error?.code!=="conversation_busy")c.attempt=null;}
    finally{c.sending=false;c.status=null;if(visibleChat(c)){const input=chatRoot().querySelector("textarea");if(input)input.value=c.draft;renderChat(c);}}
  }
  $("brand-home").addEventListener("click",()=>setView("home"));$("nav-home").addEventListener("click",()=>setView("home"));$("back-home").addEventListener("click",()=>setView("home"));
  $("nav-chat").addEventListener("click",()=>setView("chat"));$("language-ar").addEventListener("click",()=>setLanguage("ar"));$("language-en").addEventListener("click",()=>setLanguage("en"));
  $("agent-selector").addEventListener("change",()=>openAgent($("agent-selector").value,true));
  $("topic-input").addEventListener("input",()=>{if(current())current().topic=$("topic-input").value;});
  $("topic-input").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey&&!event.isComposing){event.preventDefault();$("topic-form").requestSubmit();}});
  $("topic-form").addEventListener("submit",event=>{event.preventDefault();if(current())current().topic=$("topic-input").value;generateReport();});
  $("history-menu").addEventListener("toggle",()=>{if($("history-menu").open)loadHistory();});
  for(const id of ["evidence","pack"]){
    $("tab-"+id).addEventListener("click",()=>{if(!current())return;current().tab=id;renderWorkspace();});
    $("tab-"+id).addEventListener("keydown",event=>{if(["ArrowLeft","ArrowRight","Home","End"].includes(event.key)){event.preventDefault();const next=event.key==="Home"?"evidence":event.key==="End"?"pack":id==="pack"?"evidence":"pack";current().tab=next;renderWorkspace();$("tab-"+next).focus();}});
  }
  $("mobile-chat-toggle").addEventListener("click",()=>{mobileChat=!mobileChat;$("workspace-view").classList.toggle("chat-open",mobileChat);renderWorkspace();renderVisibleChat();});
  $("pdf-retry").addEventListener("click",retryPdf);
  async function initialize(){
    setLanguage(lang);showNotice($("global-notice"),"connecting");
    try{
      await request("/v1/public/bootstrap");
      const data=await request("/v1/public/workspace/config");agents=data.agents||[];ready=true;showNotice($("global-notice"),null);setLanguage(lang);
      let previous=null;try{previous=sessionStorage.getItem("aihub.workspace.agent");}catch(_){}
      if(previous&&profile(previous))await openAgent(previous,false);
    }catch(_){showNotice($("global-notice"),"connectionError");}
  }
  initialize();
})(typeof window!=="undefined"?window:globalThis);
