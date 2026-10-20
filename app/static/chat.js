(function (global) {
  "use strict";

  const MESSAGE_LIMIT = 8000;
  const LIST_LIMIT = 50;
  const MESSAGE_PAGE = 100;
  const endpoint = "/v1/chat/conversations";

  function create(options) {
    const {root, request, renderMarkdown, getLanguage, getCurrentReport} = options;
    if (!root || typeof request !== "function") throw new TypeError("Chat requires a root and an authenticated request callback.");
    const el = (tag, className, text) => {
      const node = document.createElement(tag);
      if (className) node.className = className;
      if (text !== undefined) node.textContent = String(text);
      return node;
    };
    const t = (key, params) => global.AIHubI18n?.t(key, params) || key;
    const language = () => (typeof getLanguage === "function" ? getLanguage() : global.AIHubI18n?.getLanguage()) === "en" ? "en" : "ar";
    const button = (className, handler) => {
      const node = el("button", className);
      node.type = "button";
      node.addEventListener("click", handler);
      return node;
    };
    let loggedIn = false, epoch = 0, viewVersion = 0;
    let activeId = null, metadata = null, conversations = [], messages = [];
    let listOffset = 0, listMore = false, listLoading = false;
    let page = {has_more: false, next_before: null};
    let loading = false, loadingEarlier = false, sending = false;
    let draft = "", selectedReport = null, committedReportId = null, reportDirty = false;
    let notice = null, contextLimited = false;
    const attempts = new Map();
    const reportTitles = new Map();

    root.classList.add("chat-shell");
    root.hidden = true;
    const layout = el("div", "chat-layout");
    const history = el("aside", "chat-history");
    history.id = "aihub-chat-history";
    const historyHead = el("div", "chat-history-head");
    const historyHeading = el("h3", "chat-history-heading");
    const historyClose = button("chat-icon-button chat-history-close", () => toggleHistory(false));
    historyClose.textContent = "×";
    historyHead.append(historyHeading, historyClose);
    const newButton = button("chat-new-button", newConversation);
    const historyList = el("div", "chat-history-list");
    const moreButton = button("chat-quiet-button chat-load-more", () => loadConversations(true));
    history.append(historyHead, newButton, historyList, moreButton);
    const backdrop = button("chat-history-backdrop", () => toggleHistory(false));
    backdrop.tabIndex = -1;

    const main = el("section", "chat-main");
    const topbar = el("header", "chat-topbar");
    const historyToggle = button("chat-icon-button chat-history-toggle", () => toggleHistory(!root.classList.contains("chat-history-open")));
    historyToggle.setAttribute("aria-controls", history.id);
    const titles = el("div", "chat-titles");
    const heading = el("h2", "chat-heading");
    heading.id = "aihub-chat-heading";
    const subtitle = el("p", "chat-subtitle");
    titles.append(heading, subtitle);
    const refreshButton = button("chat-quiet-button chat-refresh", refresh);
    topbar.append(historyToggle, titles, refreshButton);

    const scroll = el("div", "chat-scroll");
    const earlierButton = button("chat-quiet-button chat-load-earlier", loadEarlier);
    const transcript = el("div", "chat-transcript");
    transcript.setAttribute("role", "log");
    transcript.setAttribute("aria-labelledby", heading.id);
    transcript.setAttribute("aria-live", "off");
    const liveStatus = el("div", "chat-live-status");
    liveStatus.setAttribute("role", "status");
    liveStatus.setAttribute("aria-live", "polite");
    scroll.append(earlierButton, transcript, liveStatus);

    const composerArea = el("div", "chat-composer-area");
    const contextArea = el("div", "chat-context-area");
    const noticeArea = el("div", "chat-notice");
    noticeArea.setAttribute("role", "alert");
    const form = el("form", "chat-composer");
    const inputLabel = el("label", "chat-sr-only");
    inputLabel.htmlFor = "aihub-chat-input";
    const input = el("textarea", "chat-input");
    input.id = inputLabel.htmlFor;
    input.rows = 2;
    input.maxLength = MESSAGE_LIMIT;
    input.dir = "auto";
    input.setAttribute("aria-describedby", "aihub-chat-keyboard aihub-chat-scope");
    const toolbar = el("div", "chat-composer-toolbar");
    const attachButton = button("chat-attach-button", attachCurrentReport);
    const sendButton = el("button", "chat-send-button");
    sendButton.type = "submit";
    toolbar.append(attachButton, sendButton);
    form.append(inputLabel, input, toolbar);
    const hints = el("div", "chat-composer-hints");
    const keyboardHint = el("span", "chat-keyboard-hint");
    keyboardHint.id = "aihub-chat-keyboard";
    const charCount = el("span", "chat-char-count");
    charCount.dir = "ltr";
    hints.append(keyboardHint, charCount);
    const scopeHint = el("p", "chat-scope-hint");
    scopeHint.id = "aihub-chat-scope";
    composerArea.append(contextArea, noticeArea, form, hints, scopeHint);
    main.append(topbar, scroll, composerArea);
    layout.append(history, backdrop, main);
    root.replaceChildren(layout);

    function conversationId(value) {
      return typeof value === "string" && /^conv_[A-Za-z0-9_-]+$/.test(value) ? value : null;
    }
    function reportId(value) {
      return typeof value === "string" && /^rpt_[A-Za-z0-9_-]+$/.test(value) ? value : null;
    }
    function currentReport() {
      if (!loggedIn) return null;
      const report = typeof getCurrentReport === "function" ? getCurrentReport() : null;
      const id = reportId(report?.id);
      if (!id) return null;
      const title = typeof report.title === "string" && report.title.trim() ? report.title.trim() : t("chat.reportUntitled");
      reportTitles.set(id, title);
      return {id, title};
    }
    function safeDate(value, withTime = false) {
      if (typeof value !== "string" || !Number.isFinite(Date.parse(value))) return "";
      return new Date(value).toLocaleString(language() === "ar" ? "ar-AE" : "en-GB", {
        timeZone: "Asia/Dubai", month: "short", day: "numeric",
        ...(withTime ? {hour: "2-digit", minute: "2-digit"} : {})
      });
    }
    function isCurrent(session, view) {
      return loggedIn && session === epoch && (view === undefined || view === viewVersion);
    }
    function setNotice(key, params, kind = "error") {
      notice = key ? {key, params, kind} : null;
      renderNotice();
    }
    function currentAttempt() { return activeId ? attempts.get(activeId) : null; }
    function hasPendingTurn() {
      return Boolean(metadata?.pending_turn_id) || messages.some(message => message.status === "pending");
    }
    function isBlocked() {
      return !loggedIn || sending || loading || hasPendingTurn() || ["unknown", "pending"].includes(currentAttempt()?.state);
    }
    function toggleHistory(open) {
      root.classList.toggle("chat-history-open", Boolean(open));
      historyToggle.setAttribute("aria-expanded", String(Boolean(open)));
      if (open) newButton.focus();
      else historyToggle.focus();
    }
    function upsertConversation(value) {
      const id = conversationId(value?.conversation_id);
      if (!id) return;
      conversations = [value, ...conversations.filter(item => item.conversation_id !== id)];
      conversations.sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
      renderHistory();
    }
    function renderHistory() {
      historyList.replaceChildren();
      if (!conversations.length) {
        historyList.append(el("p", "chat-history-empty", t(listLoading ? "chat.historyLoading" : "chat.historyEmpty")));
      }
      for (const item of conversations) {
        const id = conversationId(item?.conversation_id);
        if (!id) continue;
        const entry = button("chat-history-item", () => openConversation(id));
        entry.disabled = sending;
        if (id === activeId) { entry.classList.add("is-active"); entry.setAttribute("aria-current", "true"); }
        const title = el("span", "chat-history-title", item.title || t("chat.untitled"));
        title.dir = "auto";
        entry.append(title, el("span", "chat-history-date", safeDate(item.updated_at)));
        historyList.append(entry);
      }
      moreButton.hidden = !listMore;
      moreButton.disabled = listLoading || sending;
    }
    function renderSafeMarkdown(target, content) {
      if (typeof renderMarkdown === "function") renderMarkdown(target, content);
      else target.append(el("p", "", content));
      for (const link of target.querySelectorAll("a")) {
        const raw = link.getAttribute("href") || "";
        let safe = null;
        try {
          const url = new URL(raw);
          if (["http:", "https:"].includes(url.protocol) && !url.username && !url.password && !/[\u0000-\u0020\u007f\\]/.test(raw)) safe = url.href;
        } catch (_) { /* Unsafe links remain readable text. */ }
        if (!safe) link.replaceWith(document.createTextNode(link.textContent));
        else { link.href = safe; link.target = "_blank"; link.rel = "noopener noreferrer"; }
      }
    }
    async function copyMessage(content, control) {
      const session = epoch;
      try {
        if (!global.navigator?.clipboard?.writeText) throw new Error("Clipboard unavailable");
        await global.navigator.clipboard.writeText(content);
        if (isCurrent(session)) { control.textContent = t("chat.copied"); liveStatus.textContent = t("chat.copied"); }
      } catch (_) { if (isCurrent(session)) setNotice("chat.copyFailed"); }
    }
    function renderMessage(message) {
      const assistant = message.role === "assistant";
      const article = el("article", "chat-message " + (assistant ? "chat-message-assistant" : "chat-message-user"));
      const header = el("div", "chat-message-header");
      header.append(el("span", "chat-speaker", t(assistant ? "chat.assistant" : "chat.you")));
      const date = safeDate(message.created_at, true);
      if (date) {
        const time = el("time", "chat-message-time", date);
        time.dateTime = message.created_at;
        header.append(time);
      }
      const content = el("div", "chat-message-content");
      content.dir = "auto";
      if (assistant) renderSafeMarkdown(content, String(message.content || ""));
      else content.textContent = String(message.content || "");
      article.append(header, content);
      if (!assistant && reportId(message.report_id)) article.append(el("span", "chat-message-report", t("chat.attachedMessage")));
      if (assistant) {
        const copy = button("chat-copy-button", () => copyMessage(String(message.content || ""), copy));
        copy.textContent = t("chat.copy");
        article.append(copy);
      } else if (["failed", "interrupted"].includes(message.status)) {
        const note = el("div", "chat-message-state");
        note.append(el("p", "", t(message.status === "interrupted" ? "chat.interrupted" : "chat.failed")));
        const repeat = button("chat-quiet-button chat-send-again", () => repeatMessage(message));
        repeat.textContent = t("chat.newAttempt");
        repeat.disabled = isBlocked();
        note.append(el("p", "chat-attempt-hint", t("chat.newAttemptHint")), repeat);
        article.append(note);
      }
      return article;
    }
    function renderGreeting() {
      const greeting = el("div", "chat-greeting");
      greeting.append(el("div", "chat-greeting-mark", "AI HUB"), el("h3", "", t("chat.greeting")), el("p", "", t("chat.greetingBody")));
      const suggestions = el("div", "chat-suggestions");
      for (const key of ["Query", "Report", "Platform"]) {
        const suggestion = button("chat-suggestion", () => {
          if (isBlocked()) return;
          draft = t("chat.suggestion" + key + "Prompt");
          input.value = draft; resizeInput(); renderControls(); input.focus();
        });
        suggestion.textContent = t("chat.suggestion" + key);
        suggestion.disabled = isBlocked();
        suggestions.append(suggestion);
      }
      greeting.append(suggestions);
      return greeting;
    }
    function renderTranscript(moveToEnd = false) {
      const previousTop = scroll.scrollTop;
      transcript.replaceChildren();
      if (loading && !messages.length) transcript.append(el("p", "chat-loading", t("chat.loading")));
      else if (!messages.length) transcript.append(renderGreeting());
      else messages.forEach(message => transcript.append(renderMessage(message)));
      const pending = sending || hasPendingTurn() || currentAttempt()?.state === "pending";
      liveStatus.replaceChildren();
      if (pending) {
        const line = el("div", "chat-pending");
        const dot = el("span", "chat-pending-dot");
        dot.setAttribute("aria-hidden", "true");
        line.append(dot, el("span", "", t(sending ? "chat.sending" : "chat.pending")));
        liveStatus.append(line);
        if (!sending) {
          liveStatus.append(el("p", "chat-pending-hint", t("chat.pendingHint")));
          const check = button("chat-quiet-button", refresh);
          check.textContent = t("chat.check");
          check.disabled = loading;
          liveStatus.append(check);
        }
      }
      if (contextLimited) liveStatus.append(el("p", "chat-context-limited", t("chat.historyLimited")));
      earlierButton.hidden = !page.has_more;
      earlierButton.disabled = loadingEarlier || loading || sending;
      if (moveToEnd) scroll.scrollTop = scroll.scrollHeight;
      else scroll.scrollTop = previousTop;
    }
    function renderNotice() {
      const attempt = currentAttempt();
      noticeArea.replaceChildren();
      noticeArea.hidden = !notice && attempt?.state !== "unknown";
      if (notice) noticeArea.append(el("p", "", t(notice.key, notice.params)));
      if (attempt?.state === "unknown") {
        noticeArea.append(el("p", "chat-attempt-hint", t("chat.retryNote")));
        const retry = button("chat-quiet-button chat-retry-attempt", () => submitAttempt(attempt, true));
        retry.textContent = t("chat.retry");
        retry.disabled = sending || loading;
        const check = button("chat-quiet-button chat-check-attempt", refresh);
        check.textContent = t("chat.check");
        check.disabled = sending || loading;
        const actions = el("div", "chat-notice-actions");
        actions.append(retry, check);
        noticeArea.append(actions);
      }
    }
    function renderContext() {
      contextArea.replaceChildren();
      contextArea.hidden = !selectedReport && !reportDirty;
      if (selectedReport) {
        const chip = el("div", "chat-context-chip");
        const text = el("div", "chat-context-copy");
        text.append(el("span", "chat-context-label", t("chat.reportContext")));
        const title = el("strong", "chat-context-title", reportTitles.get(selectedReport.id) || t("chat.reportUntitled"));
        title.dir = "auto";
        text.append(title);
        const remove = button("chat-icon-button chat-remove-report", () => {
          if (isBlocked()) return;
          selectedReport = null; reportDirty = committedReportId !== null; renderContext(); renderControls();
        });
        remove.textContent = "×";
        remove.setAttribute("aria-label", t("chat.removeReport"));
        remove.disabled = isBlocked();
        chip.append(text, remove); contextArea.append(chip);
      }
      if (reportDirty) contextArea.append(el("p", "chat-context-change", t(selectedReport ? "chat.reportChanged" : "chat.reportCleared")));
    }
    function renderControls() {
      const blocked = isBlocked();
      input.disabled = !loggedIn || loading || sending;
      sendButton.disabled = blocked || !draft.trim();
      sendButton.textContent = t(sending ? "chat.sending" : "chat.send");
      newButton.disabled = sending;
      refreshButton.disabled = sending || loading || listLoading;
      attachButton.disabled = blocked || !currentReport();
      attachButton.title = t(currentReport() ? "chat.attachReport" : "chat.noReport");
      charCount.hidden = draft.length < MESSAGE_LIMIT * 0.8;
      charCount.textContent = t("chat.charCount", {count: draft.length, max: MESSAGE_LIMIT});
      form.setAttribute("aria-busy", String(sending));
      transcript.setAttribute("aria-busy", String(loading || sending));
    }
    function resizeInput() {
      input.style.height = "auto";
      input.style.height = Math.min(Math.max(input.scrollHeight || 64, 64), 208) + "px";
    }
    function setLanguage() {
      root.dir = language() === "ar" ? "rtl" : "ltr";
      root.lang = language();
      root.setAttribute("aria-label", t("chat.title"));
      history.setAttribute("aria-label", t("chat.history"));
      historyHeading.textContent = t("chat.history");
      historyToggle.textContent = t("chat.history");
      historyToggle.setAttribute("aria-label", t("chat.history"));
      historyClose.setAttribute("aria-label", t("chat.closeHistory"));
      backdrop.setAttribute("aria-label", t("chat.closeHistory"));
      newButton.textContent = "+ " + t("chat.new");
      heading.textContent = metadata?.title || t("chat.title");
      heading.dir = "auto";
      subtitle.textContent = t("chat.subtitle");
      refreshButton.textContent = t("chat.refresh");
      moreButton.textContent = t("chat.loadMore");
      earlierButton.textContent = t("chat.loadEarlier");
      inputLabel.textContent = t("chat.composerLabel");
      input.placeholder = t("chat.placeholder");
      attachButton.textContent = t("chat.attachReport");
      keyboardHint.textContent = t("chat.keyboardHint");
      scopeHint.textContent = t("chat.scopeHint");
      renderHistory(); renderTranscript(); renderContext(); renderNotice(); renderControls();
    }
    function attachCurrentReport() {
      if (isBlocked()) return;
      const report = currentReport();
      if (!report) return;
      selectedReport = report;
      reportDirty = report.id !== committedReportId;
      renderContext(); renderControls(); input.focus();
    }
    function newConversation() {
      if (!loggedIn || sending) return;
      viewVersion++;
      activeId = null; metadata = null; messages = [];
      page = {has_more: false, next_before: null};
      loading = false; loadingEarlier = false; draft = ""; input.value = "";
      selectedReport = null; committedReportId = null; reportDirty = false;
      notice = null; contextLimited = false;
      root.classList.remove("chat-history-open");
      historyToggle.setAttribute("aria-expanded", "false");
      setLanguage(); resizeInput(); input.focus();
    }
    function normalizeMessages(value) {
      return Array.isArray(value) ? value.filter(item => item && ["user", "assistant"].includes(item.role) && typeof item.content === "string") : [];
    }
    function reconcileAttempt() {
      const attempt = currentAttempt();
      if (!attempt || !attempt.turnId) return;
      const user = messages.find(item => item.turn_id === attempt.turnId && item.role === "user");
      if (!user) return;
      if (user.status === "succeeded" && messages.some(item => item.turn_id === attempt.turnId && item.role === "assistant")) {
        attempts.delete(activeId); notice = null;
      } else if (["failed", "interrupted"].includes(user.status)) attempt.state = "failed";
      else if (user.status === "pending") attempt.state = "pending";
    }
    function applyConversation(data, preserveSelection = false) {
      metadata = data;
      activeId = conversationId(data.conversation_id);
      messages = normalizeMessages(data.messages);
      page = data.pagination || {has_more: false, next_before: null};
      committedReportId = reportId(data.report_id);
      if (!preserveSelection || !reportDirty) selectedReport = committedReportId ? {id: committedReportId} : null;
      reportDirty = (selectedReport?.id || null) !== committedReportId;
      reconcileAttempt(); upsertConversation(data);
    }
    async function loadConversations(append = false) {
      if (!loggedIn || listLoading) return;
      const session = epoch;
      listLoading = true; renderHistory(); renderControls();
      const offset = append ? listOffset : 0;
      try {
        const data = await request(endpoint + "?limit=" + LIST_LIMIT + "&offset=" + offset);
        if (!isCurrent(session)) return;
        const entries = Array.isArray(data.conversations) ? data.conversations.filter(item => conversationId(item?.conversation_id)) : [];
        const combined = append ? [...conversations, ...entries] : entries;
        conversations = [...new Map(combined.map(item => [item.conversation_id, item])).values()];
        listOffset = offset + entries.length;
        listMore = entries.length === LIST_LIMIT;
      } catch (error) {
        if (isCurrent(session)) {
          if (error.status === 401) logout();
          else setNotice("chat.errorLoad");
        }
      }
      finally { if (isCurrent(session)) { listLoading = false; renderHistory(); renderControls(); } }
    }
    async function openConversation(id) {
      if (!loggedIn || sending || !conversationId(id)) return;
      const session = epoch, view = ++viewVersion;
      activeId = id; metadata = conversations.find(item => item.conversation_id === id) || null;
      messages = []; draft = ""; input.value = ""; notice = null; contextLimited = false;
      selectedReport = null; committedReportId = null; reportDirty = false;
      page = {has_more: false, next_before: null}; loading = true; loadingEarlier = false;
      root.classList.remove("chat-history-open"); historyToggle.setAttribute("aria-expanded", "false");
      setLanguage(); resizeInput();
      try {
        const data = await request(endpoint + "/" + encodeURIComponent(id) + "?limit=" + MESSAGE_PAGE);
        if (!isCurrent(session, view)) return;
        if (conversationId(data?.conversation_id) !== id) throw new Error("Invalid conversation response");
        applyConversation(data);
      } catch (error) {
        if (isCurrent(session, view)) {
          if (error.status === 401) logout();
          else setNotice(errorKey(error));
        }
      }
      finally { if (isCurrent(session, view)) { loading = false; setLanguage(); renderTranscript(true); } }
    }
    async function reloadActive(preserveNotice = false) {
      if (!activeId) return;
      const session = epoch, view = viewVersion, id = activeId;
      const data = await request(endpoint + "/" + encodeURIComponent(id) + "?limit=" + MESSAGE_PAGE);
      if (!isCurrent(session, view)) return;
      if (conversationId(data?.conversation_id) !== id) throw new Error("Invalid conversation response");
      applyConversation(data, true);
      if (!preserveNotice && currentAttempt()?.state !== "unknown") notice = null;
    }
    async function refresh() {
      if (!loggedIn || sending || loading) return;
      if (!activeId) { await loadConversations(false); return; }
      const session = epoch, view = viewVersion;
      loading = true; renderControls();
      try { await reloadActive(); }
      catch (error) {
        if (isCurrent(session, view)) {
          if (error.status === 401) logout();
          else setNotice(errorKey(error));
        }
      }
      finally { if (isCurrent(session, view)) { loading = false; setLanguage(); } }
    }
    async function loadEarlier() {
      if (!loggedIn || !activeId || !page.has_more || loadingEarlier || loading || sending || page.next_before === null) return;
      const session = epoch, view = viewVersion, id = activeId, before = page.next_before;
      const previousHeight = scroll.scrollHeight, previousTop = scroll.scrollTop;
      loadingEarlier = true; earlierButton.disabled = true;
      try {
        const data = await request(endpoint + "/" + encodeURIComponent(id) + "?limit=" + MESSAGE_PAGE + "&before=" + encodeURIComponent(before));
        if (!isCurrent(session, view)) return;
        const combined = [...normalizeMessages(data.messages), ...messages];
        messages = [...new Map(combined.map(item => [item.id, item])).values()];
        page = data.pagination || {has_more: false, next_before: null};
        renderTranscript(); scroll.scrollTop = previousTop + Math.max(0, scroll.scrollHeight - previousHeight);
      } catch (error) {
        if (isCurrent(session, view)) {
          if (error.status === 401) logout();
          else setNotice(errorKey(error));
        }
      }
      finally { if (isCurrent(session, view)) { loadingEarlier = false; earlierButton.disabled = false; } }
    }
    function errorKey(error) {
      if (error?.status === 401) return "chat.errorAuth";
      if (error?.status === 429) return "chat.errorBudget";
      if (error?.status === 404 || error?.code === "chat_report_unavailable") return "chat.errorNotFound";
      if ([413, 422].includes(error?.status)) return "chat.errorValidation";
      if (["idempotency_conflict", "chat_history_changed"].includes(error?.code)) return "chat.errorConflict";
      if (["chat_in_progress", "conversation_busy"].includes(error?.code)) return "chat.errorBusy";
      if (error?.status === 503 && !error?.detail?.turn_id) return "chat.errorUnconfigured";
      if ([502, 503, 504].includes(error?.status)) return "chat.errorProvider";
      if (!Number.isInteger(error?.status)) return "chat.errorNetwork";
      return "chat.errorGeneric";
    }
    function newKey() {
      if (typeof global.crypto?.randomUUID === "function") return "chat_" + global.crypto.randomUUID();
      if (typeof global.crypto?.getRandomValues === "function") {
        const bytes = global.crypto.getRandomValues(new Uint8Array(16));
        return "chat_" + Array.from(bytes, value => value.toString(16).padStart(2, "0")).join("");
      }
      throw new Error("Secure random identifiers unavailable");
    }
    async function sendMessage(value, reportChoice, selectedLanguage) {
      if (isBlocked()) return;
      const message = typeof value === "string" ? value.trim() : draft.trim();
      if (!message) { setNotice("chat.emptyMessage"); return; }
      if (message.length > MESSAGE_LIMIT) { setNotice("chat.tooLong", {max: MESSAGE_LIMIT}); return; }
      const session = epoch;
      sending = true; notice = null; renderControls(); renderHistory(); renderNotice();
      try {
        if (!activeId) {
          const created = await request(endpoint, "POST", {language: language(), title: ""});
          if (!isCurrent(session)) return;
          if (!conversationId(created?.conversation_id)) throw new Error("Invalid conversation response");
          activeId = created.conversation_id; metadata = created; committedReportId = reportId(created.report_id);
          upsertConversation(created);
        }
        const body = {message, language: selectedLanguage || language(), report_id: reportChoice === undefined ? selectedReport?.id || null : reportChoice};
        const attempt = {conversationId: activeId, key: newKey(), body, state: "sending", turnId: null};
        attempt.localId = "local_" + attempt.key;
        attempts.set(activeId, attempt);
        messages.push({id: attempt.localId, role: "user", content: message, report_id: body.report_id, created_at: new Date().toISOString(), status: "pending", _local: true});
        draft = ""; input.value = ""; resizeInput(); renderTranscript(true);
        await submitAttempt(attempt, false, true);
      } catch (error) {
        if (isCurrent(session)) {
          if (error.status === 401) logout();
          else setNotice(errorKey(error));
        }
      }
      finally { if (isCurrent(session)) { sending = false; setLanguage(); input.focus(); } }
    }
    async function submitAttempt(attempt, retry = false, alreadySending = false) {
      if (!loggedIn || attempt.conversationId !== activeId || (!alreadySending && sending)) return;
      const session = epoch, view = viewVersion;
      sending = true; attempt.state = "sending"; notice = null;
      renderControls(); renderHistory(); renderContext(); renderNotice(); renderTranscript(!retry);
      try {
        // The exact body and key are retained for an ambiguous retry, including its language and report.
        const data = await request(endpoint + "/" + encodeURIComponent(attempt.conversationId) + "/messages", "POST", {...attempt.body}, {"Idempotency-Key": attempt.key});
        if (!isCurrent(session, view)) return;
        if (data?.conversation_id !== attempt.conversationId || data?.message?.role !== "assistant" || typeof data.message.content !== "string" || data?.user_message?.role !== "user" || typeof data.user_message.content !== "string" || !data.message.id || !data.user_message.id) throw new Error("Invalid chat response");
        const received = [data.user_message, data.message];
        messages = messages.filter(item => item.id !== attempt.localId && !received.some(value => value.id === item.id));
        messages.push(...received);
        if (data.conversation) { metadata = data.conversation; upsertConversation(data.conversation); }
        else if (metadata) metadata = {...metadata, pending_turn_id: null};
        committedReportId = reportId(data.context?.report_id ?? attempt.body.report_id);
        selectedReport = committedReportId ? {id: committedReportId} : null; reportDirty = false;
        contextLimited = Boolean(data.context?.history_truncated);
        attempts.delete(activeId); notice = null;
        renderTranscript(true);
        if (typeof global.dispatchEvent === "function" && typeof global.CustomEvent === "function") global.dispatchEvent(new global.CustomEvent("aihub:chatupdated"));
      } catch (error) {
        if (!isCurrent(session, view)) return;
        attempt.turnId = typeof error?.detail?.turn_id === "string" ? error.detail.turn_id : attempt.turnId;
        const pending = ["chat_in_progress", "conversation_busy"].includes(error?.code);
        const rejected = [401, 403, 404, 413, 422, 429].includes(error?.status)
          || ["chat_report_unavailable", "chat_history_changed", "idempotency_conflict"].includes(error?.code)
          || (error?.status === 503 && !attempt.turnId);
        attempt.state = pending ? "pending" : attempt.turnId || error?.code === "chat_already_attempted" ? "failed" : "unknown";
        for (const item of messages.filter(item => item.id === attempt.localId)) { item.status = attempt.state; item.turn_id = attempt.turnId; }
        setNotice(errorKey(error));
        if (rejected) {
          attempts.delete(activeId); messages = messages.filter(item => item.id !== attempt.localId);
          if (!draft) { draft = attempt.body.message; input.value = draft; resizeInput(); }
        }
        if (error?.status === 401) { logout(); return; }
        try { await reloadActive(true); } catch (_) { /* Retain the attempt for an explicit, safe retry. */ }
        if (!isCurrent(session, view)) return;
        if (attempt.state === "pending" && !hasPendingTurn() && !attempt.turnId) attempt.state = "unknown";
      } finally {
        if (isCurrent(session, view)) { sending = false; setLanguage(); }
      }
    }
    async function repeatMessage(message) {
      if (isBlocked() || !["failed", "interrupted"].includes(message.status)) return;
      // This labelled user action starts a new attempt; automatic retries never create a new key.
      await sendMessage(String(message.content || ""), reportId(message.report_id), language());
    }
    input.addEventListener("input", () => { draft = input.value; resizeInput(); renderControls(); });
    input.addEventListener("keydown", event => {
      if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
        event.preventDefault();
        if (!isBlocked() && draft.trim()) return sendMessage();
      }
    });
    form.addEventListener("submit", event => { event.preventDefault(); return sendMessage(); });
    root.addEventListener("keydown", event => { if (event.key === "Escape" && root.classList.contains("chat-history-open")) toggleHistory(false); });
    async function login() {
      logout(); loggedIn = true; root.hidden = false;
      newConversation();
      await loadConversations();
    }
    function logout() {
      epoch++; viewVersion++; loggedIn = false; sending = false; loading = false; loadingEarlier = false; listLoading = false;
      activeId = null; metadata = null; conversations = []; messages = []; attempts.clear(); reportTitles.clear();
      draft = ""; input.value = ""; selectedReport = null; committedReportId = null; reportDirty = false;
      notice = null; contextLimited = false; listOffset = 0; listMore = false; page = {has_more: false, next_before: null};
      root.classList.remove("chat-history-open"); historyToggle.setAttribute("aria-expanded", "false");
      setLanguage(); root.hidden = true;
    }
    function refreshReportContext() { renderContext(); renderControls(); }
    historyToggle.setAttribute("aria-expanded", "false");
    setLanguage();
    return Object.freeze({login, logout, setLanguage, refreshReportContext});
  }
  global.AIHubChat = Object.freeze({create});
})(window);
