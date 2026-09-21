import type { NextConfig } from "next";

const allowedDevOrigins = (
  process.env.NEXT_ALLOWED_DEV_ORIGINS || "127.0.0.1,localhost"
)
  .split(",")
  .map((origin) => origin.trim());

const nextConfig: NextConfig = {
  allowedDevOrigins,

  images: {
    remotePatterns: [
      {
        protocol: "https",
        hostname: "image.tving.com",
        pathname: "/ntgs/sports/kbo/**",
      },
    ],
  },
};

export default nextConfig;