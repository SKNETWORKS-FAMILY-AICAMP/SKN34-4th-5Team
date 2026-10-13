// Deterministic NOL discovery + rendered detail. JEV lifecycle only, no model SDK.
import { CdpBrowser } from './jev/src/cdp/browser.ts';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { setTimeout as delay } from 'node:timers/promises';

const HOME = 'https://nol.yanolja.com/';
const MAX_BODY = 300_000;
const BLOCKED = /일시적으로 서비스를 이용|서비스 이용이 원활하지|Access Denied|verify you are human|Just a moment|Checking your browser|unusual traffic|로봇이 아님|보안 확인/i;
const compact = s => s.toLowerCase().replace(/[^가-힣a-z0-9]/g, '');
const [root, name, seconds = '45', directUrl] = process.argv.slice(2);
const timeoutSeconds = Number(seconds);
const timeoutMs = Math.round(timeoutSeconds * 1000);
const deadline = Date.now() + timeoutMs;
const chromePath = process.env.LODGING_CHROME_PATH;
let active;
const cancel = new AbortController();
let signal = cancel.signal;
for (const event of ['SIGTERM', 'SIGINT']) process.once(event, () => cancel.abort());

function fail(status) { throw Object.assign(new Error(status), { status }); }
function check() { if (cancel.signal.aborted) fail('cancelled'); if (signal.aborted || Date.now() >= deadline) fail('timeout'); }
function detailUrl(value) {
  try {
    const u = new URL(value);
    if (u.protocol !== 'https:' || u.hostname !== 'nol.yanolja.com' || u.port || u.username || u.password ||
        !/^\/stay\/domestic\/\d+\/?$/.test(u.pathname)) return null;
    return `${u.origin}${u.pathname.replace(/\/$/, '')}?verticalCategory=PRODUCT_CATEGORY_KOREA_ACCOMMODATION`;
  } catch { return null; }
}

async function start(label) {
  check();
  const profile = join(root, label);
  await mkdir(profile);
  const browser = active = await CdpBrowser.launch({
    executablePath: chromePath, headed: true, profileDir: profile,
    chromeArgs: '--window-size=1280,1000 ' + (process.env.LODGING_CHROME_ARGS || ''),
    signal, retryProfile: false,
  }, 'deny');
  browser.call = (method, params = {}, sessionId = browser.session) => browser.socket.call(method, params, sessionId || undefined);
  const targets = await browser.call('Target.getTargets');
  const tab = targets.targetInfos.find(t => t.type === 'page');
  if (!tab) fail('error');
  browser.session = (await browser.call('Target.attachToTarget', { targetId: tab.targetId, flatten: true })).sessionId;
  await browser.call('Page.enable');
  await browser.call('Runtime.enable');
  return browser;
}

async function close() {
  const b = active;
  active = undefined;
  await b?.close();
}

async function evaluate(b, expression) {
  check();
  const result = await b.call('Runtime.evaluate', { expression, returnByValue: true });
  if (result.exceptionDetails) fail('error');
  return result.result?.value;
}

async function snapshot(b) {
  check();
  await b.call('Page.bringToFront');
  // NOL can expose only its footer until the page has painted. Do not persist images.
  await b.call('Page.captureScreenshot', { format: 'png' });
  const doc = await evaluate(b, `(() => ({
    url:location.href, title:document.title, body:document.body?.innerText || '',
    links:[...document.querySelectorAll('a[aria-label]')].filter(e=>e.getClientRects().length)
      .map(e=>({name:e.getAttribute('aria-label').replace(/ 상품 상세 보기$/, ''),url:e.href}))
  }))()`);
  if (BLOCKED.test(doc.body) || /^(?:Just a moment|Access Denied)/i.test(doc.title)) fail('blocked');
  if (new URL(doc.url).origin !== HOME.slice(0, -1)) fail('blocked');
  if (doc.body.length > MAX_BODY) fail('overflow');
  return doc;
}

async function navigate(b, url) {
  check();
  const result = await b.call('Page.navigate', { url });
  if (result.errorText) fail('error');
  await waitDom(b, 'document.readyState === "complete" && location.href !== "about:blank"');
}

async function waitDom(b, predicate, milliseconds = 15000) {
  const until = Math.min(deadline, Date.now() + milliseconds);
  while (Date.now() < until) {
    check();
    try {
      if (await evaluate(b, predicate)) return;
    } catch (error) {
      // A navigation destroys the old execution context. Keep the same bounded wait.
      if (error.status === 'timeout') throw error;
    }
    await delay(150);
  }
  fail('timeout');
}

async function settledSnapshot(b, ready, milliseconds = 15000) {
  const until = Math.min(deadline, Date.now() + milliseconds);
  let previous = '', stableSince = 0;
  while (Date.now() < until) {
    const page = await snapshot(b);
    const value = ready(page);
    if (value && value === previous) {
      if (Date.now() - stableSince >= 750) return page;
    } else {
      previous = value || '';
      stableSince = Date.now();
    }
    await delay(250);
  }
  fail('partial');
}

async function click(b, selector) {
  const point = await evaluate(b, `(() => {
    const e=document.querySelector(${JSON.stringify(selector)});
    if(!e || !e.getClientRects().length) return null;
    e.scrollIntoView({block:'center'});
    const r=e.getBoundingClientRect(); return {x:r.x+r.width/2,y:r.y+r.height/2};
  })()`);
  if (!point) fail('not_found');
  await b.call('Input.dispatchMouseEvent', { type: 'mousePressed', ...point, button: 'left', clickCount: 1 });
  await b.call('Input.dispatchMouseEvent', { type: 'mouseReleased', ...point, button: 'left', clickCount: 1 });
}

async function collect() {
  if (directUrl !== undefined && (!/^https:\/\/nol\.yanolja\.com\/stay\/domestic\/\d+(?:\?verticalCategory=PRODUCT_CATEGORY_KOREA_ACCOMMODATION)?$/.test(directUrl) || !detailUrl(directUrl))) fail('blocked');
  if (!root || !chromePath || (directUrl === undefined && (!name?.trim() || name.length > 255)) || !Number.isFinite(timeoutSeconds) || timeoutSeconds < 1 || timeoutSeconds > 45) fail('error');
  signal = AbortSignal.any([cancel.signal, AbortSignal.timeout(timeoutMs)]);
  let b, urls = directUrl === undefined ? [] : [detailUrl(directUrl)], searchMs = 0;
  if (directUrl === undefined) {
    b = await start('search');
    const searchStarted = Date.now();
    await navigate(b, HOME);
    await snapshot(b);
    await waitDom(b, `!!document.querySelector('input[aria-label="검색어 입력"]')?.getClientRects().length`);
    await click(b, 'input[aria-label="검색어 입력"]');
    await b.call('Input.insertText', { text: name });
    await waitDom(b, `document.querySelector('input[aria-label="검색어 입력"]')?.value === ${JSON.stringify(name)} && !!document.querySelector('button[aria-label="검색"]')?.getClientRects().length`);
    const beforeSearch = await evaluate(b, 'location.href');
    await click(b, 'button[aria-label="검색"]');
    await waitDom(b, `location.href !== ${JSON.stringify(beforeSearch)} && document.readyState === 'complete'`);
    const results = await settledSnapshot(b, page => {
      const links = page.links.filter(l => detailUrl(l.url));
      return links.length ? JSON.stringify(links) : /검색 결과가 없|검색결과가 없/.test(page.body) ? page.body : '';
    });
    const links = results.links.filter(l => detailUrl(l.url));
    const exact = links.filter(l => compact(l.name) === compact(name));
    const matches = exact.length ? exact : links.filter(l => compact(l.name).endsWith(compact(name)));
    urls = [...new Set(matches.map(l => detailUrl(l.url)))];
    if (urls.length !== 1) fail(urls.length ? 'ambiguous' : 'not_found');
    await close();
    searchMs = Date.now() - searchStarted;
  }
  const detailStarted = Date.now();
  b = await start('detail');
  await navigate(b, urls[0]);
  const detailReadyAt = Date.now();
  const page = await settledSnapshot(b, page =>
    /위치\/교통\s*\n[^\n]+\n\s*주소\s*복사/.test(page.body)
      && ((/시설\/서비스|서비스 및 시설/.test(page.body) && /이용 안내|이용안내/.test(page.body))
        // Some valid properties omit these headings. Keep the previous settling
        // window for that layout instead of requiring extra facilities to exist.
        || Date.now() - detailReadyAt >= 5500) ? page.body : '');
  if (detailUrl(page.url) !== urls[0]) fail('blocked');
  const address = page.body.match(/위치\/교통\s*\n([^\n]+)\n\s*주소\s*복사/);
  const titleName = page.title.match(/^(.*?)\s+(?:호텔\/리조트|모텔|펜션|게스트하우스|리조트)\s+예약/);
  if (!address || !titleName || !compact(page.body).includes(compact(titleName[1]))) fail('partial');
  return { status: 'read', source_kind: 'rendered_dom_snapshot', url: page.url,
    title: page.title, name: titleName[1].trim(), address: address[1].trim(),
    body_text: page.body, body_read: true, collected_at: new Date().toISOString(),
    timing: { search_ms: searchMs, detail_ms: Date.now() - detailStarted } };
}

let result;
try { result = await collect(); }
catch (error) { result = { status: error.status || (cancel.signal.aborted ? 'cancelled' : signal.aborted || Date.now() >= deadline ? 'timeout' : 'error') }; }
finally { await close(); }
process.stdout.write(JSON.stringify(result));
