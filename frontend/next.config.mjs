/** @type {import('next').NextConfig} */
const backend = process.env.BACKEND_URL || "http://localhost:8000";

const nextConfig = {
  reactStrictMode: true,
  output: "standalone",
  // The browser only ever talks to this origin; /api is proxied to FastAPI.
  // No secrets or platform credentials exist in frontend code.
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/api/:path*` }];
  },
  images: { unoptimized: true },
};

export default nextConfig;
