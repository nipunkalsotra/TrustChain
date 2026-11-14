import type { Metadata } from "next"
import { Geist, Geist_Mono } from "next/font/google"
import "./globals.css"
import "@/components/ui/brand.css"
import ClientShell from "@/components/ui/ClientShell"

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"], display: "swap" })

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
})

export const metadata: Metadata = {
  title: "TrustChain — Immutable Agent Audit",
  description: "Observe AI agents, anchor their evidence, and independently verify every recorded action.",
}

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className={`${geistMono.variable} ${geistSans.variable}`}>
      <body suppressHydrationWarning>
        <ClientShell>{children}</ClientShell>
      </body>
    </html>
  )
}