"use client";

import Link from "next/link";
import { Composer } from "@/components/explorer/composer";
import { EMPTY_META, SUGGESTIONS } from "@/lib/explorer-data";

/**
 * Nothing explored yet. Not a blank page with a search box: the screen states
 * what Explorer is for — say what you are missing, not what to search for — and
 * offers three prompts the Graph derived from the gaps in the library.
 */
export function ExplorerEmpty() {
  return (
    <div className="rx-shell">
      <div className="rx-head">
        <div>
          <div className="rx-eyebrow">Explorer</div>
          <h1>Explorations</h1>
        </div>
        <div className="rx-meta">{EMPTY_META}</div>
      </div>

      <div className="rx-empty">
        <h2>Say what you are missing, not what to search for.</h2>
        <p>
          Candidates are ranked by distance to the four papers you already have,
          so the answer can tell a duplicate from a gap — and can argue against
          adding something. You can push back in plain words: fewer surveys, only
          field trials, closer to one of your own papers.
        </p>

        <div className="rx-newq">
          <Composer
            label="Start a new exploration"
            placeholder="Coverage guarantees when a UAV drops out mid-flight"
            submitLabel="Start exploring"
          />
        </div>

        <p>Three things your library is thin on, taken from the Graph:</p>
        <ul className="rx-sugg">
          {SUGGESTIONS.map((s) => (
            <li key={s.prompt}>
              <Link href={`/explorer/${s.exploration}`}>
                {s.prompt}
                <span className="rx-why">{s.why}</span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
