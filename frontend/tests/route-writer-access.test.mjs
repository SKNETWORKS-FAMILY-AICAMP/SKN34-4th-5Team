import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = name => readFileSync(join(root, "components", name), "utf8");
const writer = read("route-writer.tsx");
const planner = read("nearby-route-planner.tsx");
const guide = read("route-guide.tsx");

test("guests can load stadiums and only existing-course editing requires login", () => {
  assert.ok(writer.includes('authStatus !== "authenticated" && authStatus !== "anonymous"'));
  assert.ok(writer.includes('authStatus === "anonymous" && editId'));
  assert.ok(!writer.includes('if (authStatus === "anonymous") return'));
  assert.ok(writer.includes('user?.id ?? "guest"'));
});

test("story, both save UIs, and mobile tab references respect member permissions", () => {
  assert.ok(writer.includes('{showMemberFields && <div className="writer-writing'));
  assert.ok(writer.includes('{showMemberFields && <div className="writer-save-area"'));
  assert.ok(writer.includes('allowSave={showMemberFields}'));
  assert.ok(writer.includes('showMemberFields ? "writer-panel-write writer-panel-planner" : "writer-panel-planner"'));
  assert.ok(planner.includes('allowSave = false'));
  assert.ok(planner.includes('const canSaveCourse = allowSave && canComplete'));
  assert.ok(planner.includes('originReplacement={courseCompleted ? allowSave ?'));
});

test("guest changes neither restore nor persist member drafts", () => {
  assert.ok(writer.includes('const canPersistDraft = showMemberFields && !sample'));
  assert.ok(writer.includes('canPersistDraft ? browserDraftStorage() : undefined'));
  assert.ok(writer.includes('canPersistDraft ? readRouteDraft(storage, draftKey) : { raw: null }'));
  assert.ok(writer.includes('canPersistDraft ? writerDrafts.get(draftKey) : undefined'));
  assert.ok(writer.includes('if (!canPersistDraft) return true;'));
  assert.ok(writer.includes('if (canPersistDraft && dirty.current)'));
  const discard = writer.slice(writer.indexOf('const discardDraft'), writer.indexOf('// The chatbot'));
  assert.match(discard, /if \(canPersistDraft\) \{\s*writerDrafts.delete/);
  assert.match(discard, /useEffect\(\(\) => \{\s*if \(!canPersistDraft\) return/);
});

test("server submission remains member-only and sample guides never save", () => {
  assert.ok(writer.includes('if (sample || authStatus !== "authenticated") return;'));
  assert.ok(writer.includes('if (sample || savingRef.current) return;'));
  assert.ok(writer.includes('코스를 저장하려면 로그인해 주세요.'));
  assert.ok(writer.includes('imageUploadDisabled={sample || !showMemberFields}'));
});

test("guest guides skip both save steps", () => {
  assert.ok(guide.includes('steps: selectSteps(['));
  assert.ok(guide.includes('step.popover?.title !== "코스 저장"'));
  assert.ok(guide.includes('step.popover?.title !== "코스 저장 완료"'));
  assert.ok(writer.includes('includeMemberSteps={showMemberFields}'));
});
