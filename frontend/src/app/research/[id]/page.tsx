import { RunStream } from "@/components/run-stream";

interface Params {
  params: Promise<{ id: string }>;
}

export default async function RunPage({ params }: Params) {
  const { id } = await params;
  return <RunStream runId={id} />;
}
