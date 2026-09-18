"use client";
import { AlertCircle } from "lucide-react";
export default function ErrorPage({ reset }: { reset: () => void }) {
  return (
    <div className="p-panel p-empty">
      <AlertCircle size={30} />
      <h1>This page needs a fresh start.</h1>
      <p>We couldn’t render this workspace page. Try loading it again.</p>
      <button className="p-btn primary" onClick={reset}>
        Try again
      </button>
    </div>
  );
}
