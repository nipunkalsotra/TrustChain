"use client";
import Link from "next/link";
import {
  ArrowUpRight,
  Blocks,
  Fingerprint,
  ShieldCheck,
  Sun,
  Moon,
} from "lucide-react";
import { ThemeProvider, useTheme } from "@/lib/theme";
import "@/components/product/theme.css";
import "./auth.css";
export function AuthShell({
  tagline,
  children,
}: {
  tagline: string;
  children: React.ReactNode;
}) {
  return (
    <ThemeProvider>
      <Frame tagline={tagline}>{children}</Frame>
    </ThemeProvider>
  );
}
function Frame({
  tagline,
  children,
}: {
  tagline: string;
  children: React.ReactNode;
}) {
  const { theme, toggle } = useTheme();
  return (
    <div className="a-shell">
      <aside className="a-story">
        <Link href="/" className="a-brand">
          <ShieldCheck size={29} />
          TrustChain
        </Link>
        <div className="a-story-content">
          <div className="a-eyebrow">
            <span /> THE CONFIDENCE TO BUILD
          </div>
          <h1>
            Intelligence moves fast.
            <br />
            <em>Trust keeps up.</em>
          </h1>
          <p>
            One workspace to observe your agents, verify every action, and build
            with confidence.
          </p>
          <div className="a-visual" aria-hidden="true">
            <div className="a-orbit orbit-one" />
            <div className="a-orbit orbit-two" />
            <div className="a-visual-core">
              <ShieldCheck size={48} strokeWidth={1} />
            </div>
            <span className="a-node n1">
              <Fingerprint size={22} />
            </span>
            <span className="a-node n2">
              <Blocks size={22} />
            </span>
            <span className="a-node n3">
              <ShieldCheck size={22} />
            </span>
            <div className="a-visual-label">
              <i /> Every action. A verifiable record.
            </div>
          </div>
          <div className="a-story-note">
            <ShieldCheck size={18} />
            <span>
              Immutable evidence. Independent verification.
              <br />
              <b>Trust that goes beyond a promise.</b>
            </span>
          </div>
        </div>
        <footer>
          VERIFIABLE AI INFRASTRUCTURE <span>01 — TRUST BY DESIGN</span>
        </footer>
      </aside>
      <main className="a-main">
        <header>
          <Link href="/">
            Back to website <ArrowUpRight size={14} />
          </Link>
          <button
            className="p-icon-btn"
            aria-label="Toggle theme"
            onClick={toggle}
          >
            {theme === "light" ? <Moon size={18} /> : <Sun size={18} />}
          </button>
        </header>
        <div className="a-form">
          <div className="a-form-mark">
            <ShieldCheck size={24} />
          </div>
          <div className="p-eyebrow">YOUR TRUSTCHAIN WORKSPACE</div>
          <h1>{tagline}</h1>
          {children}
        </div>
        <footer>
          <ShieldCheck size={13} /> Protected sessions. Private by default.
        </footer>
      </main>
    </div>
  );
}
