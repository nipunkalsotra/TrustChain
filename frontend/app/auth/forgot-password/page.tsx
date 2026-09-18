"use client";
import { useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { AuthShell } from "@/components/auth/AuthShell";
import { ErrorBox, Field, message } from "@/components/product/ui";
import { forgotPassword } from "@/lib/api";
export default function Page() {
  const [email, setEmail] = useState(""),
    [busy, setBusy] = useState(false),
    [sent, setSent] = useState(false),
    [error, setError] = useState("");
  return (
    <AuthShell tagline="Forgot your password?">
      <p className="a-subtitle">
        It happens. Enter your email and we’ll help you get back into your
        workspace.
      </p>
      {sent ? (
        <div className="p-notice" role="status">
          If an account exists for {email}, a reset link is on its way. Check
          your inbox.
        </div>
      ) : (
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setBusy(true);
            setError("");
            try {
              await forgotPassword(email);
              setSent(true);
            } catch (e) {
              setError(message(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label="Email address">
            <input
              className="p-input"
              type="email"
              autoComplete="email"
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@company.com"
            />
          </Field>
          <ErrorBox error={error} />
          <button className="p-btn primary p-full" disabled={busy}>
            {busy ? "Sending…" : "Send reset link"}
          </button>
        </form>
      )}
      <Link className="a-back" href="/auth">
        <ArrowLeft size={15} />
        Back to sign in
      </Link>
    </AuthShell>
  );
}
