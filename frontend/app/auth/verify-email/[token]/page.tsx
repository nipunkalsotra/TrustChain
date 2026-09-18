"use client";
import { use, useEffect, useState } from "react";
import Link from "next/link";
import { AuthShell } from "@/components/auth/AuthShell";
import { ErrorBox, Loading, message } from "@/components/product/ui";
import { verifyEmail } from "@/lib/api";
const requests = new Map<string, Promise<unknown>>();
export default function Page({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params),
    [state, setState] = useState("loading"),
    [error, setError] = useState("");
  useEffect(() => {
    if (!requests.has(token)) requests.set(token, verifyEmail(token));
    let active = true;
    requests
      .get(token)!
      .then(() => {
        if (active) setState("done");
      })
      .catch((e) => {
        if (active) {
          setState("error");
          setError(message(e));
        }
      });
    return () => {
      active = false;
    };
  }, [token]);
  return (
    <AuthShell
      tagline={state === "done" ? "You’re verified." : "Verify your email."}
    >
      {state === "loading" ? (
        <Loading />
      ) : state === "done" ? (
        <p className="a-subtitle">
          Your email is verified. You can now create API keys and invite your
          team.
        </p>
      ) : (
        <ErrorBox error={error} />
      )}
      <Link
        className="p-btn primary p-full"
        href={state === "error" ? "/dashboard/settings" : "/dashboard"}
      >
        {state === "error"
          ? "Request a new link in Settings"
          : "Open workspace"}
      </Link>
    </AuthShell>
  );
}
