import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import ts from "typescript";

const scratch = mkdtempSync(join(tmpdir(), "kakao-map-runtime-config-"));
after(() => rmSync(scratch, { recursive: true }));

function compile(sourcePath, outputName) {
  const source = readFileSync(new URL(sourcePath, import.meta.url), "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
  }).outputText;
  writeFileSync(join(scratch, outputName), output);
  return output;
}

compile("../app/runtime-config/kakao-map/route.ts", "route.cjs");
compile("../lib/kakao-maps.ts", "kakao-maps.cjs");
const require = createRequire(join(scratch, "entry.cjs"));
const runtimeConfig = require("./route.cjs");
const { loadKakaoMaps } = require("./kakao-maps.cjs");

test("runtime config exposes only the current public map key without caching", async () => {
  const previousKey = process.env.NEXT_PUBLIC_KAKAO_MAP_KEY;
  try {
    process.env.NEXT_PUBLIC_KAKAO_MAP_KEY = "first-runtime-key";
    let response = await runtimeConfig.GET();
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.deepEqual(await response.json(), { kakaoMapKey: "first-runtime-key" });

    process.env.NEXT_PUBLIC_KAKAO_MAP_KEY = "updated-runtime-key";
    response = await runtimeConfig.GET();
    assert.deepEqual(await response.json(), { kakaoMapKey: "updated-runtime-key" });

    delete process.env.NEXT_PUBLIC_KAKAO_MAP_KEY;
    response = await runtimeConfig.GET();
    assert.equal(response.status, 503);
    assert.equal(response.headers.get("Cache-Control"), "no-store");
    assert.deepEqual(Object.keys(await response.json()), ["error"]);
  } finally {
    if (previousKey === undefined) delete process.env.NEXT_PUBLIC_KAKAO_MAP_KEY;
    else process.env.NEXT_PUBLIC_KAKAO_MAP_KEY = previousKey;
  }
});

test("map SDK loader obtains the key from the runtime endpoint", async () => {
  const originalFetch = globalThis.fetch;
  const originalWindow = globalThis.window;
  const originalDocument = globalThis.document;
  const previousKey = process.env.NEXT_PUBLIC_KAKAO_MAP_KEY;
  let script;
  let request;

  globalThis.fetch = async (url, options) => {
    request = { url, options };
    return runtimeConfig.GET();
  };
  globalThis.window = {};
  globalThis.document = {
    createElement: () => (script = { remove() {} }),
    head: {
      appendChild: element => {
        globalThis.window.kakao = { maps: { Map: function Map() {}, load: callback => callback() } };
        element.onload();
      },
    },
  };

  try {
    process.env.NEXT_PUBLIC_KAKAO_MAP_KEY = "runtime-map-key";
    const maps = await loadKakaoMaps();
    assert.ok(maps.Map);
    assert.equal(request.url, "/runtime-config/kakao-map");
    assert.equal(request.options.cache, "no-store");
    assert.match(script.src, /appkey=runtime-map-key/);
  } finally {
    globalThis.fetch = originalFetch;
    if (previousKey === undefined) delete process.env.NEXT_PUBLIC_KAKAO_MAP_KEY;
    else process.env.NEXT_PUBLIC_KAKAO_MAP_KEY = previousKey;
    if (originalWindow === undefined) delete globalThis.window;
    else globalThis.window = originalWindow;
    if (originalDocument === undefined) delete globalThis.document;
    else globalThis.document = originalDocument;
  }
});

test("production builds do not bake the public map key into the frontend image", () => {
  const workflow = readFileSync(new URL("../../.github/workflows/deploy-prod.yml", import.meta.url), "utf8");
  const compose = readFileSync(new URL("../../docker-compose.prod.yml", import.meta.url), "utf8");
  const dockerfile = readFileSync(new URL("../Dockerfile.prod", import.meta.url), "utf8");
  const frontendBuild = compose.match(/  frontend:\r?\n    build:([\s\S]*?)\r?\n    restart:/)?.[1];
  const frontendRuntime = compose.match(/  frontend:[\s\S]*?\r?\n    environment:\r?\n([\s\S]*?)\r?\n    ports:/)?.[1];

  assert.doesNotMatch(workflow, /NEXT_PUBLIC_KAKAO_MAP_KEY/);
  assert.doesNotMatch(dockerfile, /NEXT_PUBLIC_KAKAO_MAP_KEY/);
  assert.ok(frontendBuild, "production frontend build config exists");
  assert.doesNotMatch(frontendBuild, /NEXT_PUBLIC_KAKAO_MAP_KEY/);
  assert.match(frontendRuntime ?? "", /NEXT_PUBLIC_KAKAO_MAP_KEY: \$\{NEXT_PUBLIC_KAKAO_MAP_KEY:-\}/);
});
