import type { Metadata } from "next"
import { Geist_Mono } from "next/font/google"
import "./globals.css"
import ClientShell from "@/components/ui/ClientShell"  // ← add this

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
})

export const metadata: Metadata = {
  title: "TrustChain — Immutable Agent Audit",
  description: "Multi-agent AI with tamper-proof blockchain audit trail on Monad",
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={geistMono.variable}>
      <body suppressHydrationWarning>
        <ClientShell>{children}</ClientShell>  {/* ← wrap children */}
      </body>
    </html>
  )
}