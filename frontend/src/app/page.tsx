import { redirect } from "next/navigation";
import { routes } from "@/lib/routes";

/**
 * "/" belongs to the landing page. Until it is ported in, the root sends
 * visitors straight into the app so nothing dead-ends.
 */
export default function Home() {
  redirect(routes.home());
}
