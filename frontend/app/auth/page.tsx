"use client";
import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import Link from "next/link";
import { ArrowRight, Eye, EyeOff, Loader2 } from "lucide-react";
import { AuthShell } from "@/components/auth/AuthShell";
import { login, signup, getMe } from "@/lib/api";
import { setSession } from "@/lib/auth";
import { ErrorBox, Field, Loading, message } from "@/components/product/ui";
export default function AuthPage() {
  return (
    <Suspense fallback={<Loading />}>
      <AuthForm />
    </Suspense>
  );
}
function AuthForm() {
  const params = useSearchParams(),
    router = useRouter();
  const isSignup = params.get("mode") === "signup",
    invite = params.get("invite") || undefined;
  const next = params.get("next");
  const destination =
    next &&
    (next === "/dashboard" ||
      next.startsWith("/dashboard/") ||
      next.startsWith("/invite/")) &&
    !next.includes("\\")
      ? next
      : "/dashboard";
  const [name, setName] = useState(""),
    [email, setEmail] = useState(""),
    [password, setPassword] = useState(""),
    [confirm, setConfirm] = useState(""),
    [visible, setVisible] = useState(false),
    [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    if (isSignup && password !== confirm) {
      setError("Your passwords do not match.");
      return;
    }
    setBusy(true);
    try {
      if (isSignup) await signup(name.trim(), email.trim(), password, invite);
      else await login(email.trim(), password);
      const me = await getMe();
      setSession({ name: me.user.name, email: me.user.email });
      router.replace(destination);
    } catch (e) {
      setError(message(e));
      setBusy(false);
    }
  }
  const switchParams = new URLSearchParams(params.toString());
  switchParams.set("mode", isSignup ? "login" : "signup");
  return (
    <AuthShell tagline={isSignup ? "Build on trust." : "Welcome back."}>
      <p className="a-subtitle">
        {isSignup
          ? "Create your account and give your AI agents a foundation you can verify."
          : "Sign in to your workspace. Your agents and their evidence are right where you left them."}
      </p>
      <form onSubmit={submit}>
        {isSignup && (
          <Field label="Full name">
            <input
              className="p-input"
              required
              autoComplete="name"
              maxLength={100}
              placeholder="Alex Morgan"
              value={name}
              onChange={(e) => setName(e.target.value)}
            />
          </Field>
        )}
        <Field label="Email address">
          <input
            className="p-input"
            type="email"
            required
            autoComplete="email"
            placeholder="you@company.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </Field>
        <Field
          label="Password"
          hint={
            isSignup
              ? "At least 8 characters. Make it unique to this account."
              : undefined
          }
        >
          <div className="a-password">
            <input
              className="p-input"
              type={visible ? "text" : "password"}
              required
              minLength={isSignup ? 8 : 1}
              maxLength={200}
              autoComplete={isSignup ? "new-password" : "current-password"}
              placeholder="Enter your password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
            <button
              type="button"
              className="p-icon-btn"
              aria-label={visible ? "Hide password" : "Show password"}
              onClick={() => setVisible(!visible)}
            >
              {visible ? <EyeOff size={16} /> : <Eye size={16} />}
            </button>
          </div>
        </Field>
        {isSignup ? (
          <Field label="Confirm password">
            <input
              className="p-input"
              type="password"
              required
              autoComplete="new-password"
              placeholder="Re-enter your password"
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </Field>
        ) : (
          <Link className="a-forgot" href="/auth/forgot-password">
            Forgot password?
          </Link>
        )}
        <ErrorBox error={error} />
        <button className="p-btn primary p-full" disabled={busy}>
          {busy ? <Loader2 size={16} className="p-spin" /> : null}
          {busy
            ? "Opening your workspace…"
            : isSignup
              ? "Create account"
              : "Sign in"}
          {!busy && <ArrowRight size={16} />}
        </button>
      </form>
      <div className="a-divider" />
      <p className="a-switch">
        {isSignup ? "Already have an account?" : "New to TrustChain?"}
        <Link onClick={() => setError("")} href={`/auth?${switchParams}`}>
          {isSignup ? "Sign in" : "Create an account"}
        </Link>
      </p>
    </AuthShell>
  );
}
