"use client";
import { use, useState } from "react";
import Link from "next/link";
import { AuthShell } from "@/components/auth/AuthShell";
import { Field, ErrorBox, message } from "@/components/product/ui";
import { resetPassword } from "@/lib/api";
export default function Page({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params);
  const [password, setPassword] = useState(""),
    [confirm, setConfirm] = useState(""),
    [busy, setBusy] = useState(false),
    [done, setDone] = useState(false),
    [error, setError] = useState("");
  return (
    <AuthShell tagline="A fresh start.">
      <p className="a-subtitle">
        Choose a new password with at least 8 characters.
      </p>
      {done ? (
        <>
          <div className="p-notice">
            Your password has been updated. Sign in with your new password.
          </div>
          <Link className="p-btn primary p-full" href="/auth">
            Back to sign in
          </Link>
        </>
      ) : (
        <form
          onSubmit={async (e) => {
            e.preventDefault();
            setError("");
            if (password !== confirm)
              return setError("Passwords do not match.");
            setBusy(true);
            try {
              await resetPassword(token, password);
              setDone(true);
            } catch (e) {
              setError(message(e));
            } finally {
              setBusy(false);
            }
          }}
        >
          <Field label="New password">
            <input
              className="p-input"
              type="password"
              required
              autoComplete="new-password"
              minLength={8}
              maxLength={200}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </Field>
          <Field label="Confirm new password">
            <input
              className="p-input"
              type="password"
              required
              autoComplete="new-password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </Field>
          <ErrorBox error={error} />
          <button className="p-btn primary p-full" disabled={busy}>
            {busy ? "Updating…" : "Update password"}
          </button>
          <Link className="a-back" href="/auth/forgot-password">
            Request a new reset link
          </Link>
        </form>
      )}
    </AuthShell>
  );
}
