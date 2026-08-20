import { RxTheme } from "@/components/rx-theme";
import "./explorer.css";

/**
 * The Explorer subtree.
 *
 * The palette and the fonts come from `RxTheme`, which Graph also renders
 * inside; `explorer.css` carries what is Explorer's alone. Nothing outside this
 * subtree changes — see the scoping note at the top of `rx-theme.css`.
 */
export default function ExplorerLayout({ children }: { children: React.ReactNode }) {
  return <RxTheme className="rx-ex">{children}</RxTheme>;
}
