import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { runInNewContext } from "node:vm";
import ts from "typescript";

const source = readFileSync(new URL("../lib/kbo/fallback.ts", import.meta.url), "utf8");
const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS } });
const exports = {};
runInNewContext(outputText, { exports, Date, Intl });
const { supportedScheduleDate, nextCalendarDate, standingsUpdatedAt } = exports;

test("schedule dates reject arrays, malformed and impossible dates and other seasons", () => {
  for (const value of [undefined, ["2026-10-01"], "2026-2-01", "2026-02-29", "2026-04-31", "2027-01-01"])
    assert.equal(supportedScheduleDate(value), false);
  assert.equal(supportedScheduleDate("2026-10-01"), true);
});
test("tomorrow crosses month boundaries without silently clamping year boundary", () => {
  assert.equal(nextCalendarDate("2026-09-30"), "2026-10-01");
  assert.equal(nextCalendarDate("2026-12-31"), "2027-01-01");
  assert.equal(supportedScheduleDate(nextCalendarDate("2026-12-31")), false);
});
test("persisted update timestamp is displayed in Korea time", () => {
  assert.match(standingsUpdatedAt("2026-09-30T18:00:00Z"), /2026.*10.*01.*03:00/);
  assert.equal(standingsUpdatedAt(null), "확인되지 않음");
});
