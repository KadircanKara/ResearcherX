"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useParams } from "next/navigation";
import { PaperDialog } from "@/components/paper-dialog";
import { RxTheme } from "@/components/rx-theme";
import { deletePaper, getProject, listPapers, probePaperIndexed } from "@/lib/projects";
import {
  formatAdded,
  libraryHeadline,
  paperState,
  railTotal,
  sourceLine,
  stateDetail,
  summarize,
  type ProbeMap,
} from "@/lib/papers";
import type { Paper, Role } from "@/lib/types";
import "./papers.css";

const CAN_ADD: Role[] = ["owner", "editor"];

function UploadGlyph() {
  return (
    <svg
      width="20"
      height="20"
      viewBox="0 0 20 20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.3"
      aria-hidden="true"
      style={{ margin: "0 auto", color: "oklch(var(--muted-foreground))" }}
    >
      <path d="M10 13.5V3.8M6.5 7.3 10 3.8l3.5 3.5" />
      <path d="M3.5 12.5v2.7a1.3 1.3 0 0 0 1.3 1.3h10.4a1.3 1.3 0 0 0 1.3-1.3v-2.7" />
    </svg>
  );
}

export default function PapersPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const [papers, setPapers] = useState<Paper[]>([]);
  const [myRole, setMyRole] = useState<Role | null>(null);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [editing, setEditing] = useState<Paper | null>(null);

  // One row open at a time, like the concept: the opened body is wide and two
  // of them stacked push the rest of the table off screen.
  const [openId, setOpenId] = useState<string | null>(null);
  // What the retriever answered about each paper, keyed by id. Never fetched
  // in a sweep -- one request, when a row is opened. See `lib/papers.ts`.
  const [probes, setProbes] = useState<ProbeMap>({});

  const [addOpen, setAddOpen] = useState(false);
  // A fresh array per drop, deliberately: `PaperUploadScreen` consumes it on
  // identity change, so reusing one would swallow the second drop.
  const [droppedFiles, setDroppedFiles] = useState<File[]>([]);
  const [dragOver, setDragOver] = useState(false);

  // `silent` skips the full-page loading skeleton. The skeleton branch below
  // doesn't render <PaperDialog>, so a non-silent reload while the Add Paper
  // dialog is open unmounts it out from under the user — e.g. PaperUploadScreen
  // calls onSaved (this function) mid-batch, and an open dialog would vanish
  // instead of staying open to show a failed row.
  //
  // `loadSeq` guards against out-of-order resolution: onSaved (batch
  // completion) and handleDelete's error-path resync can both be in flight at
  // once, and a slower earlier request resolving after a faster later one
  // would otherwise overwrite fresher state with stale data.
  const loadSeq = useRef(0);

  const load = useCallback((opts: { silent?: boolean } = {}) => {
    const seq = ++loadSeq.current;
    if (!opts.silent) setLoading(true);
    Promise.all([listPapers(projectId), getProject(projectId)])
      .then(([ps, detail]) => {
        if (seq !== loadSeq.current) return; // a newer load already won
        setPapers(ps);
        setMyRole(detail.my_role);
        // A probe is a claim about a paper that still exists. Dropping the
        // rest keeps a deleted paper's answer from being reused by a new
        // paper that happens to reuse nothing but the shape of the map.
        setProbes((prev) => {
          const live: ProbeMap = {};
          for (const p of ps) if (prev[p.id]) live[p.id] = prev[p.id];
          return live;
        });
      })
      .catch(() => {})
      .finally(() => {
        if (seq === loadSeq.current) setLoading(false);
      });
  }, [projectId]);

  const probe = useCallback(
    (paperId: string) => {
      setProbes((prev) => ({ ...prev, [paperId]: "checking" }));
      void probePaperIndexed(projectId, paperId).then((result) => {
        setProbes((prev) =>
          // Only write back if this paper is still being tracked: a delete
          // between the request and its answer already pruned the map, and
          // re-adding the key would resurrect a row's state in `summarize`.
          prev[paperId] === undefined ? prev : { ...prev, [paperId]: result }
        );
      });
    },
    [projectId]
  );

  function toggleRow(paper: Paper) {
    const next = openId === paper.id ? null : paper.id;
    setOpenId(next);
    // Asked on open, and again only if the previous attempt failed outright.
    const seen = probes[paper.id];
    if (next && (seen === undefined || seen === "unavailable")) probe(paper.id);
  }

  async function handleDelete(paperId: string) {
    setDeleting(paperId);
    try {
      await deletePaper(projectId, paperId);
      setPapers((prev) => prev.filter((p) => p.id !== paperId));
      setProbes((prev) => {
        const next = { ...prev };
        delete next[paperId];
        return next;
      });
      if (openId === paperId) setOpenId(null);
    } catch {
      load({ silent: true });
    } finally {
      setDeleting(null);
    }
  }

  function openDialogWith(files: File[]) {
    setDroppedFiles(files);
    setAddOpen(true);
  }

  useEffect(() => {
    load();
  }, [load]);

  const canAdd = myRole !== null && CAN_ADD.includes(myRole);
  const summary = summarize(papers, probes);
  const lastAdded = papers.reduce<string | null>(
    (latest, p) => (latest === null || p.created_at > latest ? p.created_at : latest),
    null
  );

  return (
    <RxTheme className="rx-pp">
      <div className="rx-shell">
        <header className="rx-head">
          <div>
            <div className="rx-eyebrow">Library</div>
            <h1>{loading ? "Reading the library" : libraryHeadline(summary)}</h1>
          </div>
          <div className="rx-meta">
            {lastAdded ? `Last added ${formatAdded(lastAdded)}` : "Nothing added yet"}
          </div>
        </header>

        <div className="rx-pgrid">
          <div>
            <p className="rx-pintro">
              Open a paper to see what the retriever holds for it. Only papers the retriever
              holds text for can be searched or mentioned in a question.
            </p>

            <div className="rx-pcols" aria-hidden="true">
              <span>Paper</span>
              <span>Added</span>
              <span>Retriever</span>
              <span>State</span>
            </div>

            {loading ? (
              <div>
                {[0, 1, 2].map((i) => (
                  <div key={i} className="rx-pskel" />
                ))}
              </div>
            ) : papers.length === 0 ? (
              <p className="rx-pempty">
                {canAdd
                  ? "Add a paper and it is read, split and embedded on arrival — then it can be searched and mentioned in a question."
                  : "No papers have been added to this project yet."}
              </p>
            ) : (
              papers.map((paper) => {
                const state = paperState(paper, probes[paper.id]);
                const open = openId === paper.id;
                const bodyId = `rx-paper-${paper.id}`;
                return (
                  <div key={paper.id} className="rx-paper">
                    <button
                      className="rx-prow"
                      aria-expanded={open}
                      aria-controls={bodyId}
                      onClick={() => toggleRow(paper)}
                    >
                      <span className="rx-pid">
                        <span className="rx-pttl">{paper.title}</span>
                        <span className="rx-pby">{sourceLine(paper)}</span>
                      </span>
                      <span className="rx-padd">{formatAdded(paper.created_at)}</span>
                      <span className="rx-pnum">
                        {state.kind === "indexed"
                          ? "holds text"
                          : state.kind === "empty"
                            ? "holds nothing"
                            : "—"}
                      </span>
                      <span
                        className={`rx-pst${state.tone === "bad" ? " rx-pst-bad" : ""}`}
                      >
                        <span
                          className={`rx-dot${
                            state.tone === "idle"
                              ? " rx-dot-idle"
                              : state.tone === "bad"
                                ? " rx-dot-bad"
                                : ""
                          }`}
                        />
                        {state.label}
                      </span>
                    </button>

                    <div id={bodyId} className={`rx-reveal${open ? " rx-reveal-open" : ""}`}>
                      <div>
                        <div
                          className={`rx-reveal-body${state.tone === "bad" ? " rx-bad" : ""}`}
                        >
                          <div className="rx-psrc">
                            <b>{state.label}</b>{" "}
                            <span className="rx-mono">
                              {sourceLine(paper)} · added {formatAdded(paper.created_at)}
                            </span>
                          </div>
                          {stateDetail(state)}
                          {paper.abstract && (
                            <q style={{ display: "block", marginTop: 10 }}>
                              {paper.abstract.slice(0, 320)}
                              {paper.abstract.length > 320 ? "…" : ""}
                            </q>
                          )}
                          <div className="rx-pfoot">
                            <button
                              onClick={() => probe(paper.id)}
                              disabled={probes[paper.id] === "checking"}
                            >
                              Check the retriever again
                            </button>
                            {canAdd && (
                              <button onClick={() => setEditing(paper)}>Rename</button>
                            )}
                            {canAdd && (
                              <button
                                className="rx-danger"
                                disabled={deleting === paper.id}
                                onClick={() => void handleDelete(paper.id)}
                              >
                                {deleting === paper.id ? "Removing…" : "Remove from library"}
                              </button>
                            )}
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                );
              })
            )}
          </div>

          <aside className="rx-rail rx-prail" aria-label="Library summary">
            <div className="rx-flank-h">This library</div>
            <div className="rx-tot">{railTotal(summary)}</div>
            <ul className="rx-fl-list">
              <li>
                <span className="rx-ttl">Searchable</span>
                <span className="rx-d">{summary.searchable}</span>
              </li>
              <li>
                <span className="rx-ttl">Not checked yet</span>
                <span className="rx-d">{summary.unchecked}</span>
              </li>
              <li>
                <span className="rx-ttl">Needs your attention</span>
                <span className="rx-d">{summary.attention}</span>
              </li>
            </ul>

            {canAdd && (
              <button
                type="button"
                className={`rx-drop${dragOver ? " rx-drop-over" : ""}`}
                onClick={() => openDialogWith([])}
                onDragOver={(e) => {
                  e.preventDefault();
                  setDragOver(true);
                }}
                onDragLeave={() => setDragOver(false)}
                onDrop={(e) => {
                  e.preventDefault();
                  setDragOver(false);
                  // Handed STRAIGHT to the add dialog rather than filtered
                  // here: PaperUploadScreen already drops non-PDFs and caps
                  // the batch, and says so — a second copy of that rule here
                  // would silently drop files with no message at all.
                  openDialogWith(Array.from(e.dataTransfer.files));
                }}
              >
                <UploadGlyph />
                <span className="rx-drop-h">Drop a PDF here to add it to the library</span>
                <small>
                  Papers are read, split and embedded on arrival — usually under a minute for
                  20 pages.
                </small>
              </button>
            )}
          </aside>
        </div>
      </div>

      {/* Both dialogs render in a portal, OUTSIDE this wrapper, so they keep
          the app's own tokens and typeface. That is deliberate: the palette is
          scoped to the screen, and a dialog is chrome. */}
      <PaperDialog
        projectId={projectId}
        open={addOpen}
        onOpenChange={setAddOpen}
        initialFiles={droppedFiles}
        onSaved={() => load({ silent: true })}
      />

      {editing && (
        <PaperDialog
          projectId={projectId}
          paper={editing}
          open={!!editing}
          onOpenChange={(o) => !o && setEditing(null)}
          onSaved={() => {
            const editedId = editing.id;
            setEditing(null);
            // A manual paper's text is re-embedded by the PATCH, so anything
            // this screen already knew about the retriever's contents for it
            // is now a claim about the previous text.
            setProbes((prev) => {
              const next = { ...prev };
              delete next[editedId];
              return next;
            });
            // Silent: a non-silent load flips `loading` true, and that branch
            // replaces the whole table with skeletons — flashing the entire
            // list away for a single-field edit and losing scroll position.
            load({ silent: true });
          }}
        />
      )}
    </RxTheme>
  );
}
