import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-directions-test-"));
writeFileSync(join(scratch, "course-directions.js"), ts.transpileModule(readFileSync(join(frontend, "lib", "course-directions.ts"), "utf8"), { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText);
after(() => rmSync(scratch, { recursive: true }));
const require = createRequire(join(scratch, "entry.cjs"));
const { travelDistance, travelTime, validTravelPoint } = require("./course-directions.js");

test("formats provider totals and validates finite coordinate bounds", () => {
  assert.equal(travelTime(70), "2분");
  assert.equal(travelTime(3601), "1시간 1분");
  assert.equal(travelDistance(850), "850m");
  assert.equal(travelDistance(1250), "1.3km");
  assert.equal(validTravelPoint({ lat: 37.5, lng: 127.1 }), true);
  for (const value of [null, { lat: "37.5", lng: 127 }, { lat: NaN, lng: 127 }, { lat: 91, lng: 127 }, { lat: 37, lng: Infinity }]) assert.equal(validTravelPoint(value), false);
});

test("course directions use the generic Django API and no browser provider endpoint", () => {
  const source = readFileSync(join(frontend, "components", "course-travel.tsx"), "utf8");
  assert.match(source, /fetch\("\/api\/travel\/directions\/"/);
  assert.doesNotMatch(source, /apis-navi\.kakaomobility|dapi\.kakao\.com|KAKAO_REST_API_KEY/);
});
