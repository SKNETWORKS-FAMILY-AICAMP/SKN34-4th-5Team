"use client";

import { useEffect } from "react";

export function PwaRegister() {
  useEffect(() => {
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker.register("/sw.js", { scope: "/" }).catch(() => {
        // 서비스 워커 등록 실패가 앱 렌더링을 막지 않도록 처리합니다.
      });
    }
  }, []);

  return null;
}
