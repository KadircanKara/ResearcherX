import { GraphPreview } from "@/components/graph/graph-preview";

/**
 * The Graph tab: a design preview on sample data (`lib/graph-data.ts`), since
 * there is no similarity backend. It renders inside the project layout's
 * content container, like the prototype's `<Outlet/>`.
 */
export default function GraphPage() {
  return <GraphPreview />;
}
