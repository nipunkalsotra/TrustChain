import Settings from "@/components/product/Settings";
export default async function Page({ searchParams }: { searchParams: Promise<{ tab?: string }> }) {
  const { tab } = await searchParams;
  return <Settings initialTab={tab === "notifications" ? "notifications" : "workspace"} />;
}
