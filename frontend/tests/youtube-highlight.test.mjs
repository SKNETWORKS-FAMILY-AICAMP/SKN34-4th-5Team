import assert from "node:assert/strict";
import { after, test } from "node:test";
import { createRequire } from "node:module";
import { mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import ts from "typescript";

const frontend = dirname(dirname(fileURLToPath(import.meta.url)));
const scratch = mkdtempSync(join(tmpdir(), "kbo-youtube-test-"));
after(() => rmSync(scratch, { recursive: true, force: true }));

const source = readFileSync(join(frontend, "lib", "youtube", "kbo-highlight.ts"), "utf8");
const { outputText } = ts.transpileModule(source, {
  fileName: "kbo-highlight.ts",
  compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS },
});
writeFileSync(join(scratch, "kbo-highlight.js"), outputText);

const requireTestModule = createRequire(join(scratch, "entry.cjs"));
const { isKboLeagueHighlightTitle } = requireTestModule("./kbo-highlight.js");

test("accepts an official regular-season full game highlight", () => {
  assert.equal(isKboLeagueHighlightTitle(
    "[LG트윈스 vs 한화이글스] 9.9(수) 야구 하이라이트｜2026 KBO 리그｜KBO X TVING",
  ), true);
});

test("accepts official postseason full game highlights with the title sponsor", () => {
  assert.equal(isKboLeagueHighlightTitle(
    "[KT위즈 vs 삼성라이온즈] 10.14(수) 야구 하이라이트｜2026 신한 SOL KBO 포스트시즌｜KBO X TVING",
  ), true);
  assert.equal(isKboLeagueHighlightTitle(
    "진짜 가을은 집으로 돌아가는 여정에서 시작된다. | 2026 신한 SOL KBO 포스트시즌 | 10월 14일 D-Day",
  ), false);
});

test("rejects Futures League and short-form highlight videos", () => {
  assert.equal(isKboLeagueHighlightTitle(
    "[2026 메디힐 KBO 퓨처스리그 H/L] 한화 vs LG 야구 하이라이트｜KBO X TVING",
  ), false);
  assert.equal(isKboLeagueHighlightTitle(
    "크보모먼트 | KBO 야구 하이라이트 모음｜2026 KBO 리그｜KBO X TVING",
  ), false);
  assert.equal(isKboLeagueHighlightTitle(
    "9회말 끝내기 홈런 #Shorts｜2026 KBO 리그｜KBO X TVING",
  ), false);
});
