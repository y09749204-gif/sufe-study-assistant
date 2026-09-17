export function parseSubtitles(raw) {
  const clock = value => value.replace(',', '.').split(':').reduce((sum, part) => sum * 60 + Number(part), 0);
  const segments = [];
  for (const block of raw.replace(/\r/g, '').split(/\n\s*\n/)) {
    const match = block.match(/((?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})\s*-->\s*((?:\d{2}:)?\d{2}:\d{2}[.,]\d{3})[^\n]*\n([\s\S]*)/);
    if (!match) continue;
    const text = match[3].replace(/<[^>]*>/g, '').trim();
    const start = clock(match[1]), end = clock(match[2]);
    if (text && end >= start) segments.push({start, end, text});
  }
  return {segments, text: segments.length ? segments.map(s => s.text).join('\n') : raw.replace(/^WEBVTT[^\n]*\n/, '').trim()};
}

export function replayLinks(entries, baseUrl) {
  return [...new Map(entries.filter(item => {
    try { const url = new URL(item.href, baseUrl); return url.origin === new URL(baseUrl).origin && /^\/learn\/videoreview\/[^/]+\/?$/.test(url.pathname) && item.label; }
    catch { return false; }
  }).map(item => [item.href, item])).values()];
}

export function assertAuthenticated(body, hasLoginLink) {
  if (hasLoginLink || /统一身份认证|请输入密码/.test(body)) throw new Error('login_required: 空中课堂登录已过期，请重新登录');
  if (!body.includes('我参与的')) throw new Error('page_changed: 无法确认本人参与课程筛选');
}

export function episodeDate(weekRange, dateLabel) {
  const range = [...weekRange.matchAll(/(\d{4})\.(\d{2})\.(\d{2})/g)].map(m => `${m[1]}-${m[2]}-${m[3]}`);
  const day = dateLabel.match(/(\d{1,2})月(\d{1,2})日/);
  if (range.length !== 2 || !day) throw new Error('page_changed: 课次日期不完整');
  const possibilities = [...new Set(range.map(value => value.slice(0, 4)))].map(year => `${year}-${day[1].padStart(2,'0')}-${day[2].padStart(2,'0')}`)
    .filter(value => value >= range[0] && value <= range[1]);
  if (possibilities.length !== 1) throw new Error('page_changed: 课次日期不属于显示的教学周');
  return possibilities[0];
}

// Serializable callback for Playwright evaluateAll; preserve the source TeX once,
// rather than duplicating KaTeX's accessibility and visual rendering trees.
export function transcriptParagraphs(nodes) {
  return nodes.map(node => {
    const timestamp = node.querySelector('.point_in')?.textContent?.trim();
    if (!/^\d{2}:\d{2}:\d{2}$/.test(timestamp || '')) return null;
    const clone = node.cloneNode(true);
    clone.querySelector('.point_in')?.remove();
    for (const math of clone.querySelectorAll('.katex')) {
      const tex = math.querySelector('annotation[encoding="application/x-tex"]')?.textContent;
      if (tex) math.replaceWith(` $${tex}$ `);
    }
    return {start: timestamp.split(':').reduce((sum, part) => sum * 60 + Number(part), 0),
      text: (clone.textContent || '').replace(/\s+/g, ' ').trim()};
  }).filter(row => row?.text);
}
