// Run against the freshly bundled collector in an offline, sandbox-enabled Node 24 container.
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const collector = process.argv[2];
assert.ok(collector, 'Pass the freshly bundled chrome-lodging.mjs path');
assert.equal(process.getuid(), 1000, 'Run as the non-root node user');
assert.equal(process.versions.node.split('.')[0], '24');
for (const [seconds, name, expected] of [
  ['1.001', '롯데시티호텔 마포', 'timeout'],
  ['1.0001', '롯데시티호텔 마포', 'timeout'],
  ['1.5', '롯데시티호텔 마포', 'timeout'],
  ['1', '롯데시티호텔 마포', 'timeout'],
  ['45', '롯데시티호텔 마포', undefined],
  [undefined, '롯데시티호텔 마포', undefined],
  ...['NaN', 'Infinity', '-Infinity', '1e100', '0', '-1', '0.9999', '45.0001', '60', '', 'abc']
    .map(seconds => [seconds, '롯데시티호텔 마포', 'error']),
  ['1.001', '', 'error'],
]) {
  const root = await mkdtemp(join(tmpdir(), 'lodging-timeout-'));
  try {
    const args = [collector, root, name];
    if (seconds !== undefined) args.push(seconds);
    const child = spawn(process.execPath, args, { stdio: ['ignore', 'pipe', 'pipe'] });
    let stdout = '', stderr = '';
    child.stdout.on('data', data => { stdout += data; });
    child.stderr.on('data', data => { stderr += data; });
    const watchdog = setTimeout(() => child.kill('SIGKILL'), 60000);
    const [code, signal] = await new Promise((resolve, reject) => {
      child.once('error', reject);
      child.once('exit', (code, signal) => resolve([code, signal]));
    });
    clearTimeout(watchdog);
    assert.equal(code, 0, `${seconds}: ${signal || ''} ${stderr}`);
    const output = JSON.parse(stdout);
    if (expected === 'error') assert.deepEqual(output, { status: 'error', stage: 'startup' }, `${seconds}: ${stderr}`);
    else {
      assert.ok(['startup', 'search_navigation', 'search_results', 'detail_startup', 'detail_navigation', 'detail_content'].includes(output.stage), stdout);
      if (expected) assert.equal(output.status, expected, `${seconds}: ${stderr}`);
      else assert.ok(['timeout', 'error'].includes(output.status), stdout);
      assert.deepEqual(output, { status: output.status, stage: output.stage }, stdout);
    }
    assert.ok(!stderr.includes('ERR_OUT_OF_RANGE'), stderr);
    console.log(JSON.stringify({ seconds: seconds ?? 'default', name, code, output: JSON.parse(stdout) }));
  } finally {
    await rm(root, { recursive: true, force: true, maxRetries: 10, retryDelay: 100 });
  }
}
console.log('PASS: fractional, integer, default, and invalid timeout envelopes');
