import { GraphScreen } from "@/components/graph/graph-screen";
import { RxTheme } from "@/components/rx-theme";
import "./graph.css";

/**
 * The Graph tab.
 *
 * `RxTheme` scopes the Institution/Console palette and the concept's typefaces
 * to this subtree and nothing else — Chat, Papers and LaTeX render in the app's
 * own tokens and in Inter, exactly as before. `rx-gr` carries what is this
 * screen's alone, the 2080px shell cap above all.
 *
 * Nothing on this screen fetches. It is a design preview on the concept's own
 * sample corpus, and it says so on the screen itself.
 */
export default function GraphPage() {
  return (
    <RxTheme className="rx-gr">
      <div className="rx-shell">
        <GraphScreen />
      </div>
    </RxTheme>
  );
}
