import type { Metadata } from "next"
import { Inter } from "next/font/google"
import { PricingComingSoon } from "@/components/landing/components/PricingComingSoon"

/** Spec §14.2 — the one route that gets a short cinematic placeholder. */

const inter = Inter({ subsets: ["latin"], variable: "--tc-font-sans", display: "swap", weight: ["400", "500", "600"] })

export const metadata: Metadata = {
  title: "Pricing — TrustChain",
  robots: { index: false },
}

export default function Page() {
  return (
    <div className={inter.variable}>
      <PricingComingSoon />
    </div>
  )
}
