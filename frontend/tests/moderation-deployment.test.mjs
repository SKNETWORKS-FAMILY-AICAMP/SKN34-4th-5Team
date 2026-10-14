import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
const read = name => readFileSync(new URL(`../../${name}`, import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const service = (text, name) => text.match(new RegExp(`^  ${name}:\\n([\\s\\S]*?)(?=^  [a-z][\\w-]*:|^volumes:|$(?![\\s\\S]))`, 'm'))?.[1];
test('development and production deploy explicitly start the moderation worker without EC2 builds', () => {
  for (const name of ['dev', 'prod']) {
    const workflow = read(`.github/workflows/deploy-${name}.yml`);
    const commands = workflow.replace(/\\\r?\n\s*/g, ' ');
    assert.match(commands, /up -d --no-build[^\n]*--wait[^\n]*community-moderation-worker/);
    assert.match(workflow, /-t dev-backend:latest/);
    assert.match(workflow, /docker save[\s\S]*?dev-backend:latest/);
  }
});
test('workers wait for migrated healthy backend and share its deployment configuration', () => {
  for (const name of ['docker-compose.yml', 'docker-compose.prod.yml']) {
    const text = read(name).replace(/\r\n/g, '\n');
    const worker = service(text, 'community-moderation-worker');
    assert.ok(worker);
    assert.match(worker, /command: \["python", "manage.py", "run_moderation_worker"\]/);
    assert.match(worker, /backend:\n\s+condition: service_healthy/);
    assert.match(worker, /stop_grace_period: 40s/);
    assert.match(service(text, 'backend'), /healthcheck:/);
    assert.match(service(text, 'backend'), /python manage.py migrate/);
    assert.match(worker, /environment: \*(moderation|backend)-environment/);
    if (name.includes('prod')) assert.match(worker, /image: dev-backend:latest/);
  }
  assert.match(service(read('docker-compose.dev.yml'), 'community-moderation-worker'), /image: dev-backend:latest[\s\S]*build: !reset null/);
});
