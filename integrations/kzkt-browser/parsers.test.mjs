import test from 'node:test';
import assert from 'node:assert/strict';
import {parseSubtitles, replayLinks, assertAuthenticated, episodeDate, transcriptParagraphs} from './parsers.mjs';

test('VTT and SRT retain timing and mathematical text', () => {
  for (const input of ['WEBVTT\n\n00:01.500 --> 00:03.000\n<b>矩阵 AB</b>', '1\n00:00:01,500 --> 00:00:03,000\n矩阵 AB']) {
    assert.deepEqual(parseSubtitles(input), {text:'矩阵 AB', segments:[{start:1.5,end:3,text:'矩阵 AB'}]});
  }
  assert.deepEqual(parseSubtitles('纯文本'), {text:'纯文本',segments:[]});
});
test('all pages can be merged without truncation or foreign links', () => {
  const entries = Array.from({length:150}, (_, i) => ({href:`https://dm.shufe.edu.cn/learn/videoreview/${i}`,label:'线性代数'}));
  assert.equal(replayLinks([...entries,...entries,{href:'https://other.test/learn/videoreview/1',label:'外部'}], 'https://dm.shufe.edu.cn').length,150);
});
test('login and unknown page are errors, not empty success', () => {
  assert.throws(() => assertAuthenticated('我参与的 暂无数据',true), /login_required/);
  assert.throws(() => assertAuthenticated('未知页面',false), /page_changed/);
  assert.doesNotThrow(() => assertAuthenticated('我参与的 暂无数据',false));
});
test('lesson date belongs to its displayed week, including year boundary', () => {
  assert.equal(episodeDate('2026.09.14 - 2026.09.20', '9月15日'), '2026-09-15');
  assert.equal(episodeDate('2026.12.28 - 2027.01.03', '1月2日'), '2027-01-02');
  assert.throws(() => episodeDate('2026.09.14 - 2026.09.20', '8月15日'));
});
test('speech extraction preserves TeX once and excludes the timestamp', async () => {
  const {default: pw} = await import('playwright-core');
  const browser = await pw.chromium.launch({headless:true,...(process.env.SUFE_TEST_BROWSER ? {executablePath:process.env.SUFE_TEST_BROWSER} : {})});
  try {
    const page = await browser.newPage();
    await page.setContent('<div class="paragraph"><div class="point_in">00:11:51</div><span>当 <span class="katex"><span class="katex-mathml"><math><semantics><annotation encoding="application/x-tex">a_{11}a_{22}</annotation></semantics></math></span><span class="katex-html">重复渲染</span></span> 非零时。</span></div>');
    assert.deepEqual(await page.locator('.paragraph').evaluateAll(transcriptParagraphs), [{start:711,text:'当 $a_{11}a_{22}$ 非零时。'}]);
  } finally {await browser.close();}
});
