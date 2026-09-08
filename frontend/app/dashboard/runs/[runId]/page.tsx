import type { Metadata } from "next"

export const metadata: Metadata = {
  title: "Run Detail — TrustChain",
}

// Intentionally empty for now — see app/dashboard/page.tsx's comment. The
// [runId] segment itself is what keeps this route real; nothing renders it
// yet since there's no run data behind it.
export default function DashboardRunDetailPage() {
  return null
}
