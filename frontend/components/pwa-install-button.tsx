"use client";

import { useEffect, useState } from "react";

interface BeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: "accepted" | "dismissed" }>;
}

export function PwaInstallButton({ onInstalled }: { onInstalled?: () => void }) {
  const [installEvent, setInstallEvent] = useState<BeforeInstallPromptEvent | null>(null);

  useEffect(() => {
    const handleBeforeInstallPrompt = (event: Event) => {
      event.preventDefault();
      setInstallEvent(event as BeforeInstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", handleBeforeInstallPrompt);
    return () => window.removeEventListener("beforeinstallprompt", handleBeforeInstallPrompt);
  }, []);

  if (!installEvent) return null;

  async function install() {
    await installEvent?.prompt();
    const choice = await installEvent?.userChoice;
    setInstallEvent(null);
    if (choice?.outcome === "accepted") onInstalled?.();
  }

  return (
    <button className="header-menu-category" type="button" onClick={install}>
      <span className="header-menu-icon" aria-hidden="true">↓</span>
      <span>앱으로 설치</span>
    </button>
  );
}
