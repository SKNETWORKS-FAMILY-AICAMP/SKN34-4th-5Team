import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "KBO ROUTE | 직관의 하루를, 나답게",
    short_name: "KBO ROUTE",
    description: "경기 전 맛집부터 경기 후 산책까지. 나만의 야구 직관 코스를 만들고 함께 나누는 공간.",
    start_url: "/",
    scope: "/",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#246bf3",
    icons: [
      {
        src: "/pwa-192.png",
        sizes: "192x192",
        type: "image/png",
      },
      {
        src: "/pwa-512.png",
        sizes: "512x512",
        type: "image/png",
        purpose: "maskable",
      },
    ],
  };
}
