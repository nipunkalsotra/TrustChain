/** The real TrustChain mark (frontend/public/logo_transparent.png — the
 * asset already in the project; a previous pass referenced a
 * trustchain-logo.png that was never actually added, which is why the logo
 * was broken everywhere), used in every sidebar/modal logo slot so they
 * never drift to different assets. A plain <img> rather than next/image: a
 * static icon has nothing for its optimizer/responsive pipeline to do, and
 * this renders exactly what's in the src attribute with no additional
 * Next-specific dev/error chrome. */
export function TrustChainLogo({ size = 22, className }: { size?: number; className?: string }) {
  // eslint-disable-next-line @next/next/no-img-element
  return <img src="/logo_transparent.png" alt="TrustChain" width={size} height={size} className={className} />
}
