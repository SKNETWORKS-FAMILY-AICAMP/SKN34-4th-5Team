import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import ts from "typescript";

function load(path, dependencies = {}) {
  const source = readFileSync(new URL(path, import.meta.url), "utf8");
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } });
  const module = { exports: {} };
  new Function("module", "exports", "require", outputText)(module, module.exports, name => dependencies[name]);
  return module.exports;
}
const categories = load("../lib/community-post-category.ts");
const filter = load("../lib/community-category-filter.ts", { "./community-post-category": categories });

test("category links preserve team/search, leave detail and reset page", () => {
  const url = new URL(filter.communityCategoryHref("/community/teams?team=LT&q=검색&post=abc&page=3", "사진·영상"), "http://local");
  assert.equal(url.searchParams.get("team"), "LT");
  assert.equal(url.searchParams.get("q"), "검색");
  assert.equal(url.searchParams.get("category"), "사진·영상");
  assert.equal(url.searchParams.has("post"), false);
  assert.equal(url.searchParams.has("page"), false);
  assert.equal(filter.communityCategoryHref("/community?category=질문", ""), "/community");
});

test("category uses exact match including punctuation and legacy categories", () => {
  assert.equal(filter.matchesCommunityCategory({category: "사진·영상"}, "사진·영상"), true);
  assert.equal(filter.matchesCommunityCategory({category: "사진·영상"}, "사진"), false);
  assert.equal(filter.matchesCommunityCategory({category: "잡담"}, ""), true);
  assert.equal(filter.isCommunityCategory("소식·정보"), true);
  assert.equal(filter.isCommunityCategory("없는분류"), false);
});

test("list tags are separate links and category is part of pagination scope", () => {
  const source = readFileSync(new URL("../components/community-board.tsx", import.meta.url), "utf8");
  assert.ok(source.includes('styles.titleLinks}>{categoryLink(post.category)}<Link'));
  assert.ok(source.includes("activeSearch.field, query, category]"));
  assert.ok(source.includes('categoryHref("")'));
});
