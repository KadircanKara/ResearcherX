"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { Download, FileCode2, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ImportDropzone } from "@/components/latex/import-dropzone";
import { NewDocumentDialog } from "@/components/latex/new-document-dialog";
import {
  AmbiguousMainError,
  createDocument,
  deleteDocument,
  downloadExport,
  importArchive,
  listDocuments,
  errorText,
  type LatexDocument,
} from "@/lib/latex";
import { getProject } from "@/lib/projects";
import { STARTER } from "@/lib/latex-starter";
import type { Role } from "@/lib/types";

const CAN_EDIT: Role[] = ["owner", "editor"];

function fmtDate(iso: string) {
  return new Date(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
}

export default function LatexIndexPage() {
  const { id: projectId } = useParams<{ id: string }>();
  const router = useRouter();

  const [docs, setDocs] = useState<LatexDocument[]>([]);
  const [role, setRole] = useState<Role | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const [newOpen, setNewOpen] = useState(false);
  const [importOpen, setImportOpen] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [importCandidates, setImportCandidates] = useState<string[]>([]);
  const [importError, setImportError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [rows, detail] = await Promise.all([
        listDocuments(projectId),
        getProject(projectId),
      ]);
      setDocs(rows);
      setRole(detail.my_role);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }, [projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const canEdit = role !== null && CAN_EDIT.includes(role);

  async function handleCreate(name: string) {
    setError(null);
    try {
      const doc = await createDocument(projectId, { name, source: STARTER });
      router.push(`/research/${projectId}/latex/${doc.id}`);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function handleDelete(doc: LatexDocument) {
    // A LaTeX project is a whole file tree and there is no undo -- the one
    // action on this page that cannot be taken back gets an explicit
    // confirmation, unlike export.
    if (!window.confirm(`Delete "${doc.name}" and all of its files? This cannot be undone.`)) {
      return;
    }
    setBusyId(doc.id);
    setError(null);
    try {
      await deleteDocument(projectId, doc.id);
      setDocs((prev) => prev.filter((d) => d.id !== doc.id));
    } catch (err) {
      setError(errorText(err));
      void load();
    } finally {
      setBusyId(null);
    }
  }

  async function handleExport(doc: LatexDocument) {
    setBusyId(doc.id);
    setError(null);
    try {
      await downloadExport(projectId, doc.id, doc.name);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusyId(null);
    }
  }

  function handleImport(zip: File, name: string, mainPath?: string) {
    setImportBusy(true);
    setImportError(null);
    setImportCandidates([]);
    importArchive(projectId, zip, name, mainPath)
      .then((doc) => {
        setImportOpen(false);
        router.push(`/research/${projectId}/latex/${doc.id}`);
      })
      .catch((err) => {
        // Rethrown unchanged by `importArchive` so the dropzone can render
        // its candidate picker -- this is the only failure that is not an
        // error message.
        if (err instanceof AmbiguousMainError) {
          setImportCandidates(err.candidates);
          return;
        }
        setImportError(errorText(err));
      })
      .finally(() => setImportBusy(false));
  }

  if (loading) {
    return (
      <div className="space-y-3">
        {[1, 2, 3].map((i) => (
          <div key={i} className="h-20 animate-pulse rounded-xl bg-muted" />
        ))}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-4 flex items-center justify-between gap-3">
        <p className="text-sm text-muted-foreground">
          {docs.length === 0
            ? "No LaTeX projects yet"
            : `${docs.length} project${docs.length !== 1 ? "s" : ""}`}
        </p>
        {canEdit && (
          <Button size="sm" onClick={() => setNewOpen(true)}>
            <Plus className="mr-1.5 size-3.5" />
            New project
          </Button>
        )}
      </div>

      {error && (
        <p className="mb-3 rounded-md border border-destructive/40 bg-destructive/10 px-3 py-2 text-sm text-destructive">
          {error}
        </p>
      )}

      {docs.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-24 text-center">
          <FileCode2 className="size-8 text-muted-foreground/40" />
          <p className="text-sm text-muted-foreground">
            {canEdit
              ? "Start a blank paper, or import a .zip from Overleaf."
              : "No LaTeX projects have been created yet."}
          </p>
        </div>
      ) : (
        <div className="space-y-2">
          {docs.map((doc) => (
            <div
              key={doc.id}
              className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3"
            >
              {/* The whole name is the link, but the row's buttons are not
                  inside it -- an <a> wrapping the actions would make Export
                  navigate as well as download. */}
              <Link
                href={`/research/${projectId}/latex/${doc.id}`}
                className="min-w-0 flex-1"
              >
                <p className="line-clamp-1 text-sm font-medium text-foreground">{doc.name}</p>
                <p className="mt-1 truncate font-mono text-xs text-muted-foreground">
                  {doc.main_path}
                </p>
                <p className="mt-1.5 text-xs text-muted-foreground/60">
                  {doc.engine} · updated {fmtDate(doc.updated_at)}
                </p>
              </Link>

              <button
                onClick={() => void handleExport(doc)}
                disabled={busyId === doc.id}
                title="Download .zip"
                aria-label={`Download ${doc.name} as .zip`}
                className="shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-accent hover:text-foreground disabled:opacity-40"
              >
                <Download className="size-4" />
              </button>

              {canEdit && (
                <button
                  onClick={() => void handleDelete(doc)}
                  disabled={busyId === doc.id}
                  title="Delete project"
                  aria-label={`Delete ${doc.name}`}
                  className="shrink-0 rounded p-1 text-muted-foreground/50 transition-colors hover:bg-destructive/10 hover:text-destructive disabled:opacity-40"
                >
                  <Trash2 className="size-4" />
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      <NewDocumentDialog
        open={newOpen}
        onClose={() => setNewOpen(false)}
        onCreateBlank={(name) => void handleCreate(name)}
        onChooseImport={() => {
          setImportCandidates([]);
          setImportError(null);
          setImportOpen(true);
        }}
      />

      <ImportDropzone
        open={importOpen}
        busy={importBusy}
        error={importError}
        candidates={importCandidates}
        onClose={() => {
          setImportOpen(false);
          setImportCandidates([]);
          setImportError(null);
        }}
        onImport={handleImport}
      />
    </div>
  );
}
