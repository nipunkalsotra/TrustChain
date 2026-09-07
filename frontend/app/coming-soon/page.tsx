import type { Metadata } from "next"
import { Suspense } from "react"
import { Inter } from "next/font/google"
import { ComingSoon, ComingSoonShell } from "@/components/landing/components/ComingSoonPage"

/**
 * Simple Coming Soon page (spec §14.1).
 *
 * "Used for Docs, View Docs, Log In and Get Started. Plain white or black
 * background, simple TrustChain branding, Coming Soon message and Back/Return
 * action. No Three.js world, no large animation, no unnecessary engineering
 * because these pages will be replaced later."
 *
 * Taken literally: there is no WebGL here and no shared landing machinery
 * imported, so this route's bundle is a few kilobytes rather than dragging in
 * three.js for a page that exists to be deleted.
 */

const inter = Inter({ subsets: ["latin"], variable: "--tc-font-sans", display: "swap", weight: ["400", "500", "600"] })

export const metadata: Metadata = {
  title: "Coming Soon — TrustChain",
  robots: { index: false },
}

export default function Page() {
  return (
    <div className={inter.variable}>
      {/* useSearchParams needs a Suspense boundary in the App Router; without
          it this route silently opts the whole page into client rendering. */}
      <Suspense fallback={<ComingSoonShell />}>
        <ComingSoon />
      </Suspense>
    </div>
  )
}
