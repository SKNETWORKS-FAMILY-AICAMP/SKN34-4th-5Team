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
    const archive = `${name === 'dev' ? 'development' : 'production'}-images.tar.gz`;
    for (const image of ['backend', 'frontend', 'jev-browser']) {
      assert.match(commands, new RegExp(`docker buildx build [^\\n]*--load[^\\n]*-t dev-${image}:latest`));
    }
    assert.match(commands, /docker tag dev-backend:latest dev-lodging-warmer:latest/);
    assert.match(commands, /docker save\s+dev-backend:latest\s+dev-lodging-warmer:latest\s+dev-frontend:latest\s+dev-jev-browser:latest/);
    assert.ok(commands.includes(`| gzip -1 > "$RUNNER_TEMP/${archive}"`));
    assert.ok(commands.includes(`"$EC2_USER@$EC2_HOST:${archive}"`));
    assert.match(commands, new RegExp(`docker load -i (?:"\\$HOME/|~/)${archive.replaceAll('.', '\\.')}"?`));
    assert.match(workflow, /max_attempts=6/);
    assert.match(workflow, /--connect-timeout 2 --max-time 10/);
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
