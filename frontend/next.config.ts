import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Pin Turbopack's workspace root to this directory. Without this, Next.js
  // auto-detects a workspace root by walking up for lockfiles and finds TWO
  // (this repo's root package-lock.json, for release tooling — see
  // package.json's own description — and this directory's own) and picks the
  // wrong one (repo root), which caused real, reproduced failures: dev-server
  // requests intermittently 500'ing with "Could not find the module ... in
  // the React Client Manifest" (RSC bundler getting confused about what's
  // actually in this app), and is the likely cause of a real JS heap OOM
  // crash after ~9 minutes of dev-server uptime — Turbopack watching/bundling
  // far more of the monorepo (contracts/lib's OpenZeppelin submodule, .venv,
  // etc.) than this app actually needs. See
  // https://nextjs.org/docs/app/api-reference/config/next-config-js/turbopack#root-directory
  turbopack: {
    root: __dirname,
  },
};

export default nextConfig;
