"use client";
import { use, useCallback, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { AuthShell } from "@/components/auth/AuthShell";
import { getMe, request, switchProject } from "@/lib/api";
import {
  ErrorBox,
  Loading,
  message,
  useResource,
} from "@/components/product/ui";
export default function Page({
  params,
}: {
  params: Promise<{ token: string }>;
}) {
  const { token } = use(params),
    router = useRouter();
  const preview = useResource(
    useCallback(
      () =>
        request<{
          orgName: string;
          role: string;
          email: string;
          invitedByName: string;
        }>(`/invitations/${encodeURIComponent(token)}`),
      [token],
    ),
  );
  const [busy, setBusy] = useState(false),
    [error, setError] = useState("");
  const next = `/invite/${encodeURIComponent(token)}`;
  return (
    <AuthShell tagline="You’re invited.">
      {preview.loading ? (
        <Loading />
      ) : preview.error ? (
        <ErrorBox error={preview.error} retry={preview.reload} />
      ) : (
        <>
          <p className="a-subtitle">
            {preview.data?.invitedByName} invited {preview.data?.email} to join{" "}
            <strong>{preview.data?.orgName}</strong> as a {preview.data?.role}.
          </p>
          <ErrorBox error={error} />
          <button
            className="p-btn primary p-full"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError("");
              try {
                await getMe();
                const accepted = await request<{
                  membership: { orgId: number };
                }>(`/invitations/${encodeURIComponent(token)}/accept`, "POST");
                const me = await getMe();
                const project = me.memberships.find(
                  (m) => m.org.id === accepted.membership.orgId,
                )?.projects[0];
                if (project) await switchProject(project.id);
                router.replace("/dashboard");
              } catch (e) {
                if (e instanceof Error && "status" in e && e.status === 401)
                  router.push(`/auth?next=${encodeURIComponent(next)}`);
                else {
                  setError(message(e));
                  setBusy(false);
                }
              }
            }}
          >
            {busy ? "Joining…" : "Accept invitation"}
          </button>
          <div className="a-divider" />
          <p className="a-switch">
            New to TrustChain?{" "}
            <Link
              href={`/auth?mode=signup&invite=${encodeURIComponent(token)}`}
            >
              Create your account
            </Link>
          </p>
          <Link
            className="a-back"
            href={`/auth?next=${encodeURIComponent(next)}`}
          >
            Sign in to your existing account
          </Link>
        </>
      )}
    </AuthShell>
  );
}
