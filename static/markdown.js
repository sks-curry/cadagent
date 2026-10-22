// A deliberately small Markdown renderer. Model content is always DOM text;
// raw HTML and executable links are never inserted into the page.
const element = (tag, className = '') => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  return node;
};

export function appendInline(node, text) {
  const pattern = /(\x60[^\x60\n]+\x60|\*\*[^*\n]+\*\*|\[[^\]\n]+\]\([^\s)]+\))/g;
  let end = 0;
  for (const match of text.matchAll(pattern)) {
    node.append(document.createTextNode(text.slice(end, match.index)));
    const token = match[0];
    if (token.charCodeAt(0) === 96) {
      const code = element('code'); code.textContent = token.slice(1, -1); node.append(code);
    } else if (token.startsWith('**')) {
      const strong = element('strong'); strong.textContent = token.slice(2, -2); node.append(strong);
    } else {
      const parts = token.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
      let safe = false;
      try { safe = ['https:', 'http:'].includes(new URL(parts[2]).protocol); } catch {}
      if (safe) {
        const link = element('a'); link.textContent = parts[1]; link.href = parts[2];
        link.target = '_blank'; link.rel = 'noopener noreferrer'; node.append(link);
      } else node.append(document.createTextNode(token));
    }
    end = match.index + token.length;
  }
  node.append(document.createTextNode(text.slice(end)));
}

const cells = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());
const tableRule = line => line.includes('|') && cells(line).every(cell => /^:?-{3,}:?$/.test(cell));
const fence = line => /^\x60{3}/.test(line);
const bullet = line => /^\s*(?:[-*]|\d+\.)\s+/.test(line);

export function renderMarkdown(container, text) {
  const lines = String(text).replace(/\r\n/g, '\n').split('\n');
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (!line.trim()) { i++; continue; }
    if (fence(line)) {
      const language = line.replace(/^\x60{3}/, '').trim().slice(0, 40);
      const collected = []; i++;
      while (i < lines.length && !fence(lines[i])) collected.push(lines[i++]);
      if (i < lines.length) i++;
      const block = element('div', 'code-block'), header = element('div', 'code-header');
      const label = element('span'); label.textContent = language || 'code';
      const copy = element('button'); copy.type = 'button'; copy.textContent = '复制代码';
      const raw = collected.join('\n');
      copy.onclick = async () => {
        try { await navigator.clipboard.writeText(raw); copy.textContent = '已复制'; setTimeout(() => copy.textContent = '复制代码', 1800); }
        catch { copy.textContent = '复制失败'; }
      };
      const pre = element('pre'), code = element('code'); code.textContent = raw;
      pre.append(code); header.append(label, copy); block.append(header, pre); container.append(block); continue;
    }
    if (i + 1 < lines.length && line.includes('|') && tableRule(lines[i + 1])) {
      const wrap = element('div', 'table-wrap'), table = element('table'), head = element('thead'), row = element('tr');
      for (const text of cells(line)) { const th = element('th'); appendInline(th, text); row.append(th); }
      head.append(row); table.append(head); i += 2;
      const body = element('tbody');
      while (i < lines.length && lines[i].includes('|') && lines[i].trim()) {
        const tr = element('tr');
        for (const text of cells(lines[i++])) { const td = element('td'); appendInline(td, text); tr.append(td); }
        body.append(tr);
      }
      table.append(body); wrap.append(table); container.append(wrap); continue;
    }
    if (/^#{1,6}\s+/.test(line)) {
      const title = element('h3'); appendInline(title, line.replace(/^#{1,6}\s+/, '')); container.append(title); i++; continue;
    }
    if (bullet(line)) {
      const ordered = /^\s*\d+\./.test(line), list = element(ordered ? 'ol' : 'ul');
      while (i < lines.length && bullet(lines[i]) && /^\s*\d+\./.test(lines[i]) === ordered) {
        const item = element('li'); appendInline(item, lines[i++].replace(/^\s*(?:[-*]|\d+\.)\s+/, '')); list.append(item);
      }
      container.append(list); continue;
    }
    const paragraph = element('p');
    do {
      if (paragraph.childNodes.length) paragraph.append(element('br'));
      appendInline(paragraph, lines[i++]);
    } while (i < lines.length && lines[i].trim() && !fence(lines[i]) && !bullet(lines[i]) && !/^#{1,6}\s/.test(lines[i]) && !(i + 1 < lines.length && tableRule(lines[i + 1])));
    container.append(paragraph);
  }
}
