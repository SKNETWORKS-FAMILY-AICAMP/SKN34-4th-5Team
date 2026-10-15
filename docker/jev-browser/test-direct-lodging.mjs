// Offline trust/challenge checks against the real collector functions, no browser/network.
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

const source = (await readFile(new URL('./chrome-lodging.mjs', import.meta.url), 'utf8')).replace(/\r\n/g, '\n');
const declarations = source.replace(/^import .*;\n/gm, '').replace(/^export /gm, '').split('\nif (process.argv[1]')[0];
const url = 'https://nol.yanolja.com/stay/domestic/3012752';
const context = vm.createContext({ process: { argv: ['node', 'collector', '/tmp/test', '', '45', url], env: { LODGING_CHROME_PATH: '/chrome' }, once() {} }, URL, Date, AbortController, AbortSignal, setTimeout });
vm.runInContext(declarations + '\nglobalThis.api = { collect, snapshot, detailUrl };', context);
const page = { url, title: '호텔 토마스 명동 호텔/리조트 예약', readyState: 'complete', body: '호텔 토마스 명동\n위치/교통\n서울특별시 중구 세종대로16길 26\n주소복사', links: [] };
const browser = { call: async method => method === 'Runtime.evaluate' ? { result: { value: page } } : {} };
assert.equal((await context.api.snapshot(browser)).body, page.body);
for (const [title, body] of [['Just a moment...', 'Enable JavaScript and cookies to continue'], ['Access Denied', ''], ['NOL', 'verify you are human']]) {
  page.title = title; page.body = body;
  await assert.rejects(context.api.snapshot(browser), error => error.status === 'blocked');
}
for (const input of [url.replace('https:', 'http:'), url.replace('.com/', '.com:443/'), url.replace('//', '//user:pass@'), url + '?foo=bar', url + '#x', url.replace('3012752', '../3012752')]) {
  const invalid = vm.createContext({ process: { argv: ['node', 'collector', '/tmp/test', '', '45', input], env: { LODGING_CHROME_PATH: '/chrome' }, once() {} }, URL, Date, AbortController, AbortSignal, setTimeout });
  vm.runInContext(declarations + '\nglobalThis.collect = collect;', invalid);
  await assert.rejects(invalid.collect(), error => error.status === 'blocked');
}
console.log('PASS actual collector snapshot challenge/success and six pre-launch URL rejections');
