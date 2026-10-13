import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdtemp, readFile, writeFile, chmod, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { createServer } from 'node:http';
import { CdpBrowser } from './jev/src/cdp/browser.ts';

const root = await mkdtemp(join(tmpdir(), 'lifecycle-check-'));
const executable = join(root, 'chrome');
const receipt = join(root, 'args');
await writeFile(executable, `#!/bin/sh\nprintf '%s\\n' "$$" "$@" >> '${receipt}'\nexec /usr/bin/chromium "$@"\n`);
await chmod(executable, 0o755);
const servers = [];
const serve = async handler => {
  const server = createServer(handler);
  server.listen(0, '0.0.0.0');
  await once(server, 'listening');
  servers.push(server);
  return server.address().port;
};
const framePort = await serve((req, res) => { res.setHeader('Content-Type', 'text/html'); res.end('<main>FRAME_END</main>'); });
const port = await serve((req, res) => {
  if (req.url === '/hang') return;
  res.setHeader('Content-Type', 'text/html');
  res.end(`<main>BEGIN<iframe src="http://localhost:${framePort}/article" title="article"></iframe>END</main>`);
});
const gone = async pid => {
  for (let i = 0; i < 50; i++) {
    try { process.kill(pid, 0); } catch { return; }
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.fail(`Chrome ${pid} survived cleanup`);
};
const readers = [];
const waitForReceipt = async (reader, marker, timeout = 15000) => {
  const deadline = Date.now() + timeout;
  while (true) {
    assert.ok(reader.exitCode === null && reader.signalCode === null, 'Reader exited before Chrome startup');
    if ((await readFile(receipt, 'utf8').catch(error => {
      if (error.code === 'ENOENT') return '';
      throw error;
    })).includes(marker)) return;
    assert.ok(Date.now() < deadline, 'Reader startup receipt deadline exceeded');
    await new Promise(resolve => setTimeout(resolve, 100));
  }
};
try {
  for (const earlyExit of [true, false]) {
    const child = spawn(process.execPath, ['-e', earlyExit ? 'process.exit(1)' : 'setInterval(() => {}, 1000)'], { detached: true });
    readers.push(child);
    const exit = once(child, 'exit');
    if (earlyExit) await exit;
    await assert.rejects(waitForReceipt(child, 'never-written-marker', 200), earlyExit ? /exited/ : /deadline/);
    if (!earlyExit) { process.kill(-child.pid, 'SIGKILL'); await exit; }
  }
  for (const headed of [false, true]) {
    await writeFile(executable, `#!/bin/sh\nprintf '%s\\n' "$$" "$@" >> '${receipt}'\nexec ${headed ? '/usr/bin/google-chrome' : '/usr/bin/chromium'} "$@"\n`);
    const profileDir = join(root, headed ? 'search' : 'detail');
    const b = await CdpBrowser.launch({ executablePath: executable, profileDir, headed,
      chromeArgs: '--disable-dev-shm-usage --proxy-server="http://127.0.0.1:9" --proxy-bypass-list="<-loopback>"',
      signal: AbortSignal.timeout(15000), retryProfile: false }, 'deny');
    const args = (await readFile(receipt, 'utf8')).trim().split('\n');
    const pid = Number(args[args.lastIndexOf(`--user-data-dir=${profileDir}`) - 2]);
    assert.ok(args.includes(`--user-data-dir=${profileDir}`));
    assert.ok(args.includes('--proxy-server=http://127.0.0.1:9'));
    assert.ok(args.includes('--proxy-bypass-list=<-loopback>'));
    const latest = args.slice(args.lastIndexOf(`--user-data-dir=${profileDir}`) - 2);
    assert.equal(latest.includes('--headless=new'), !headed);
    await b.close();
    await gone(pid);
  }
  for (const abort of ['timeout', 'cancel']) {
    const controller = new AbortController();
    const signal = abort === 'timeout' ? AbortSignal.timeout(15000) : controller.signal;
    const b = await CdpBrowser.launch({ executablePath: executable, profileDir: join(root, abort),
      chromeArgs: '--disable-dev-shm-usage', signal, retryProfile: false }, 'deny');
    const { targetId } = await b.socket.call('Target.createTarget', { url: 'about:blank' });
    const { sessionId } = await b.socket.call('Target.attachToTarget', { targetId, flatten: true });
    const pending = b.socket.call('Runtime.evaluate', { expression: 'new Promise(()=>{})', awaitPromise: true }, sessionId);
    if (abort === 'cancel') controller.abort();
    await assert.rejects(pending);
    await b.close();
  }
  const failed = await CdpBrowser.launch({ executablePath: '/no/chrome', profileDir: join(root, 'missing'),
    signal: AbortSignal.timeout(200), retryProfile: false }, 'deny').then(() => false, () => true);
  assert.ok(failed);
  const cancelledProfile = join(root, 'cancelled-reader');
  const cancelledReader = spawn(process.execPath, ['/opt/service/read-body.mjs', `http://127.0.0.1:${port}/hang`, cancelledProfile], {
    env: { ...process.env, CHROME_PATH: executable, JEV_CHROME_ARGS: '--disable-dev-shm-usage', HTTP_PROXY: '', HTTPS_PROXY: '' },
    stdio: ['ignore', 'pipe', 'inherit'], detached: true,
  });
  readers.push(cancelledReader);
  let cancelledOutput = '';
  cancelledReader.stdout.on('data', chunk => cancelledOutput += chunk);
  const cancelledExit = once(cancelledReader, 'exit');
  await waitForReceipt(cancelledReader, `--user-data-dir=${cancelledProfile}`);
  await new Promise(resolve => setTimeout(resolve, 2000));
  process.kill(-cancelledReader.pid, 'SIGTERM');
  await cancelledExit;
  assert.equal(JSON.parse(cancelledOutput).status, 'cancelled');
  const cancelledArgs = (await readFile(receipt, 'utf8')).trim().split('\n');
  await gone(Number(cancelledArgs[cancelledArgs.lastIndexOf(`--user-data-dir=${cancelledProfile}`) - 2]));
  const profile = join(root, 'reader');
  const reader = spawn(process.execPath, ['/opt/service/read-body.mjs', `http://127.0.0.1:${port}/`, profile], {
    env: { ...process.env, CHROME_PATH: executable, JEV_CHROME_ARGS: '--disable-dev-shm-usage', HTTP_PROXY: '', HTTPS_PROXY: '' },
    stdio: ['ignore', 'pipe', 'inherit'],
  });
  let output = '';
  reader.stdout.on('data', chunk => output += chunk);
  const [code] = await once(reader, 'exit');
  assert.equal(code, 0);
  const result = JSON.parse(output);
  assert.equal(result.status, 'ok');
  assert.equal(result.body, 'BEGINEND\n\nFRAME_END');
  assert.ok(result.frames.some(frame => frame.url.includes(`localhost:${framePort}`) && frame.chars === 9));
  assert.equal(result.extractor_version, 'jev-dom-v1');
  console.log('PASS real JEV launch: override, headed/headless, isolated profiles, proxy, timeout/cancel, missing executable, reader cross-origin body parity');
} finally {
  for (const reader of readers) {
    if (reader.exitCode !== null || reader.signalCode !== null) continue;
    const exit = once(reader, 'exit');
    try { process.kill(-reader.pid, 'SIGKILL'); } catch (error) { if (error.code !== 'ESRCH') throw error; }
    await exit;
  }
  for (const server of servers) { server.closeAllConnections(); server.close(); }
  await rm(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
}
