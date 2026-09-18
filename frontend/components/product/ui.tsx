"use client";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowRight,
  Check,
  Copy,
  Download,
  Loader2,
  Search,
  ShieldCheck,
  X,
} from "lucide-react";
import Link from "next/link";
import { createPortal } from "react-dom";

export function useResource<T>(loader: () => Promise<T>, poll = 0) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(0);
  const reload = useCallback(() => setRevision((n) => n + 1), []);
  useEffect(() => {
    let active = true;
    const run = async () => {
      try {
        const value = await loader();
        if (active) {
          setData(value);
          setError("");
        }
      } catch (e) {
        if (active) setError(message(e));
      } finally {
        if (active) setLoading(false);
      }
    };
    void run();
    const timer = poll ? setInterval(run, poll) : undefined;
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [loader, poll, revision]);
  return { data, error, loading, reload };
}
export const message = (e: unknown) =>
  e instanceof Error ? e.message : "Something went wrong. Please try again.";
export const date = (value?: number | null) =>
  value
    ? new Date(value * 1000).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "2-digit",
        minute: "2-digit",
      })
    : "—";
export const short = (value?: string | null) =>
  value
    ? value.length > 22
      ? `${value.slice(0, 12)}…${value.slice(-6)}`
      : value
    : "—";
export function download(name: string, data: unknown) {
  const url = URL.createObjectURL(
    new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
  );
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}
export function PageTitle({
  eyebrow = "WORKSPACE",
  title,
  description,
  children,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  children?: React.ReactNode;
}) {
  return (
    <header className="p-heading">
      <div>
        <div className="p-eyebrow">{eyebrow}</div>
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="p-actions">{children}</div>
    </header>
  );
}
export function Badge({
  children,
  tone = "",
}: {
  children: React.ReactNode;
  tone?: string;
}) {
  return (
    <span className={`p-badge ${tone}`}>
      <i />
      {children}
    </span>
  );
}
export function Status({ value }: { value: string }) {
  const tone = [
    "complete",
    "confirmed",
    "resolved",
    "active",
    "verified",
  ].includes(value)
    ? "green"
    : ["error", "critical", "revoked", "mismatch"].includes(value)
      ? "red"
      : ["running", "pending", "warning", "acknowledged"].includes(value)
        ? "amber"
        : "";
  return <Badge tone={tone}>{value}</Badge>;
}
export function ErrorBox({
  error,
  retry,
}: {
  error: string;
  retry?: () => void;
}) {
  return error ? (
    <div className="p-error" role="alert">
      <AlertCircle size={18} />
      <span>{error}</span>
      {retry && (
        <button className="p-btn small" onClick={retry}>
          Try again
        </button>
      )}
    </div>
  ) : null;
}
export function Loading() {
  return (
    <div className="p-loading" role="status">
      <Loader2 size={22} className="p-spin" />
      Loading your workspace…
    </div>
  );
}
export function Empty({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children?: React.ReactNode;
}) {
  return (
    <div className="p-empty">
      <div className="p-empty-icon">
        <ShieldCheck size={25} />
      </div>
      <h3>{title}</h3>
      <p>{description}</p>
      {children}
    </div>
  );
}
export function Panel({
  title,
  description,
  action,
  children,
  className = "",
}: {
  title?: string;
  description?: string;
  action?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section className={`p-panel ${className}`}>
      {title && (
        <div className="p-panel-head">
          <div>
            <h2>{title}</h2>
            {description && <p>{description}</p>}
          </div>
          {action}
        </div>
      )}
      {children}
    </section>
  );
}
export function SearchField({
  value,
  onChange,
  placeholder = "Search…",
}: {
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <label className="p-search">
      <Search size={16} />
      <input
        aria-label={placeholder}
        placeholder={placeholder}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </label>
  );
}
export function Field({
  label,
  children,
  hint,
}: {
  label: string;
  children: React.ReactNode;
  hint?: string;
}) {
  return (
    <label className="p-field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}
export function Modal({
  title,
  description,
  close,
  children,
}: {
  title: string;
  description?: string;
  close: () => void;
  children: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const closeRef = useRef(close);
  useEffect(() => {
    closeRef.current = close;
  }, [close]);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const old = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    ref.current?.focus();
    function key(e: KeyboardEvent) {
      if (e.key === "Escape") closeRef.current();
      if (e.key === "Tab") {
        const nodes = ref.current?.querySelectorAll<HTMLElement>(
          'button:not(:disabled), input, select, textarea, a[href], [tabindex="0"]',
        );
        if (!nodes?.length) return;
        const first = nodes[0],
          last = nodes[nodes.length - 1];
        if (
          e.shiftKey &&
          (document.activeElement === first ||
            document.activeElement === ref.current)
        ) {
          e.preventDefault();
          last.focus();
        } else if (
          !e.shiftKey &&
          (document.activeElement === last ||
            document.activeElement === ref.current)
        ) {
          e.preventDefault();
          first.focus();
        }
      }
    }
    document.addEventListener("keydown", key);
    return () => {
      document.body.style.overflow = old;
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, []);
  const host =
    typeof document !== "undefined" ? document.querySelector(".tc-app") : null;
  if (!host) return null;
  return createPortal(
    <div
      className="p-modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) close();
      }}
    >
      <div
        className="p-modal"
        role="dialog"
        aria-modal="true"
        aria-label={title}
        ref={ref}
        tabIndex={-1}
      >
        <button
          aria-label="Close dialog"
          className="p-icon-btn p-modal-close"
          onClick={close}
        >
          <X size={20} />
        </button>
        <h2>{title}</h2>
        {description && <p className="p-muted">{description}</p>}
        {children}
      </div>
    </div>,
    host,
  );
}
export function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState("");
  return (
    <>
      <button
        type="button"
        className="p-btn small"
        aria-label="Copy to clipboard"
        onClick={async () => {
          try {
            await navigator.clipboard.writeText(value);
            setCopied(true);
          } catch {
            setError(
              "Clipboard unavailable. Select and copy the value manually.",
            );
          }
        }}
      >
        {copied ? <Check size={14} /> : <Copy size={14} />}
        {copied ? "Copied" : "Copy"}
      </button>
      {error && <small role="alert">{error}</small>}
    </>
  );
}
export function ExportButton({
  data,
  name = "trustchain-export.json",
}: {
  data: unknown;
  name?: string;
}) {
  return (
    <button className="p-btn" onClick={() => download(name, data)}>
      <Download size={15} />
      Export JSON
    </button>
  );
}
export function TextLink({
  href,
  children,
}: {
  href: string;
  children: React.ReactNode;
}) {
  return (
    <Link className="p-text-link" href={href}>
      {children}
      <ArrowRight size={15} />
    </Link>
  );
}
