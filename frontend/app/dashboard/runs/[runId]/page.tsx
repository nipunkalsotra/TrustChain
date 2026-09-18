import RunDetail from "@/components/product/RunDetail";
export default async function Page({
  params,
}: {
  params: Promise<{ runId: string }>;
}) {
  const { runId } = await params;
  return <RunDetail id={runId} />;
}
