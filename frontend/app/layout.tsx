import type { Metadata, Viewport } from "next";
import "@fontsource-variable/noto-sans-kr";
import "./globals.css";
import "@/styles/navigation.css";
import "@/styles/home.css";
import "@/styles/schedule.css";
import "@/styles/youtube-highlight.css";
import "@/styles/routes.css";
import "@/styles/community.css";
import "@/styles/writer.css";
import "@/styles/info.css";
import "@/styles/auth.css";
import "@/styles/chat-launcher.css";
import "@/styles/map.css";
import "@/styles/nearby-planner.css";
import "@/styles/course-travel.css";
import "@/styles/stadium-parking.css";
import { ChatProvider } from "@/components/chat-provider";
import { SiteFrame } from "@/components/site-frame";
import { MemberAuthProvider } from "@/lib/member-auth";

export const metadata: Metadata = {
  title: { default: "KBO ROUTE | 직관의 하루를, 나답게", template: "%s | KBO ROUTE" },
  description: "경기 전 맛집부터 경기 후 산책까지. 나만의 야구 직관 코스를 만들고 함께 나누는 공간.",
  referrer: "no-referrer",
};

export const viewport: Viewport = {
  themeColor: "#246bf3",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="ko" data-scroll-behavior="smooth">
      <body>
        <MemberAuthProvider>
          <ChatProvider><SiteFrame>{children}</SiteFrame></ChatProvider>
        </MemberAuthProvider>
      </body>
    </html>
  );
}
