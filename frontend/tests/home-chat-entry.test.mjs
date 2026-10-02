import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";

const home = readFileSync(new URL("../components/home-page.tsx", import.meta.url), "utf8");

test("guest hero search including AI opens chat rather than login", () => {
  const start = home.indexOf('authStatus !== "authenticated" ? <Link');
  assert.ok(start >= 0);
  const guestLink = home.slice(start, home.indexOf("</Link>", start));
  assert.ok(guestLink.includes('href="/chat"'));
  assert.ok(guestLink.includes('aria-label="직관 도우미 채팅창 열기"'));
  assert.ok(guestLink.includes('className="hero-chat-ai">AI'));
  assert.ok(!guestLink.includes("openChat(question)"));
  assert.ok(!guestLink.includes('href="/login"'));
});

test("member hero retains its existing question submission", () => {
  assert.ok(home.includes('if (authStatus === "authenticated") openChat(question)'));
  assert.ok(home.includes("maxLength={MAX_MESSAGE_LENGTH}"));
});
