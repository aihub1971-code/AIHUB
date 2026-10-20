(function(global){
  "use strict";
  function safeArticleLink(value){
    if(typeof value!=="string"||/[\u0000-\u0020\u007f\\]/.test(value))return null;
    try{const url=new URL(value),host=url.hostname.toLowerCase().replace(/\.$/,"");
      if(!["https:","http:"].includes(url.protocol)||url.username||url.password||url.port||!host.includes(".")||!/^[a-z0-9.-]+$/.test(host)||/^[\d.]+$/.test(host)||/(?:^|\.)(?:localhost|local|internal|lan|home|invalid)$/.test(host))return null;
      return url.href;
    }catch(_){return null;}
  }
  function markdownDestination(text, start){
    if(text[start] === "<"){
      const end = text.indexOf(">", start + 1);
      if(end < 0 || text[end + 1] !== ")") return null;
      return {url: text.slice(start + 1, end), end: end + 2};
    }
    let depth = 0;
    for(let index = start; index < text.length; index++){
      const char = text[index];
      if(/[\s<>\\]/.test(char)) return null;
      if(char === "(") depth++;
      else if(char === ")"){
        if(depth === 0) return {url: text.slice(start, index), end: index + 1};
        depth--;
      }
    }
    return null;
  }
  function appendLink(parent, label, url){
    const href = safeArticleLink(url);
    if(!href){parent.append(document.createTextNode(label));return;}
    const a = document.createElement("a");
    a.textContent = label; a.href = href; a.target = "_blank"; a.rel = "noopener noreferrer";
    parent.append(a);
  }
  function appendInline(parent, source){
    const text = String(source || "");
    const pattern = /\[([^\]\r\n]+)\]\((?=<?https?:\/\/)|\*\*([^*]+)\*\*|\*([^*]+)\*|`([^`]+)`|(https?:\/\/[^\s<>]+)/g;
    let cursor = 0, match;
    while((match = pattern.exec(text)) !== null){
      if(match.index > cursor) parent.append(document.createTextNode(text.slice(cursor, match.index)));
      if(match[1] !== undefined){
        // A source URL can contain balanced parentheses of its own. Read to
        // the outer Markdown delimiter rather than cutting at the first ')'.
        const destination = markdownDestination(text, pattern.lastIndex);
        if(destination){appendLink(parent, match[1], destination.url);pattern.lastIndex = destination.end;}
        else parent.append(document.createTextNode(match[0]));
      }else if(match[2] !== undefined){
        const el = document.createElement("strong"); el.textContent = match[2]; parent.append(el);
      }else if(match[3] !== undefined){
        const el = document.createElement("em"); el.textContent = match[3]; parent.append(el);
      }else if(match[4] !== undefined){
        const el = document.createElement("code"); el.textContent = match[4]; parent.append(el);
      }else if(match[5] !== undefined){
        let url = match[5], suffix = "";
        while(/[.,;:!?]$/.test(url)){ suffix = url.slice(-1) + suffix; url = url.slice(0,-1); }
        appendLink(parent, url, url);
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
  global.AIHubMarkdown={render:renderMarkdown,inline:appendInline,safeLink:safeArticleLink};
})(window);
