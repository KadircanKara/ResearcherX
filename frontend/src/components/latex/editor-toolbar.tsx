"use client";

import Link from "next/link";
import { ArrowLeft, Loader2, MoreVertical, Play, RefreshCw, Share2 } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import type { LatexEngine } from "@/lib/latex";
import { routes } from "@/lib/routes";

export type ViewMode = "source" | "split" | "pdf";

// The engine choice is ALREADY made for the user at import: xelatex when the
// source loads fontspec/unicode-math/polyglossia, which hard-fail under
// pdflatex. Someone changing it needs the reason more than the control.
const ENGINE_HINT =
  "pdflatex is faster and is what most publisher templates assume. Switch to xelatex if the document loads fontspec, unicode-math or polyglossia, or needs a system font or a non-Latin script.";

/**
 * The editor's header bar, ported from the prototype's `LatexToolbar`.
 *
 * The prototype's "More actions" menu holds Rename and Delete; the real
 * engine's other document-level actions -- download the PDF, export the
 * project, pick the TeX engine -- live in the same menu so the bar itself
 * stays exactly the prototype's.
 */
export function EditorToolbar({
  projectId,
  name,
  mainPath,
  engine,
  canEdit,
  viewMode,
  onViewModeChange,
  compiling,
  onCompile,
  pdfStale,
  onShare,
  onRename,
  onDelete,
  onEngineChange,
  canDownloadPdf,
  onDownloadPdf,
  onExport,
}: {
  projectId: string;
  name: string;
  mainPath: string;
  engine: LatexEngine;
  canEdit: boolean;
  viewMode: ViewMode;
  onViewModeChange: (mode: ViewMode) => void;
  compiling: boolean;
  onCompile: () => void;
  /**
   * The PDF on screen no longer matches the project (`dirty || revision !==
   * compiled.revision`). Shown in the prototype's amber "stale" slot, whose
   * action is to bring the preview back in step -- here, by compiling.
   */
  pdfStale: boolean;
  onShare: () => void;
  onRename: () => void;
  onDelete: () => void;
  onEngineChange: (engine: LatexEngine) => void;
  canDownloadPdf: boolean;
  onDownloadPdf: () => void;
  onExport: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-2 border-b bg-card px-3 py-2">
      <Button variant="ghost" size="sm" className="gap-1.5" render={<Link href={routes.latex(projectId)} />}>
        <ArrowLeft className="size-3.5" aria-hidden />
        Back to project
      </Button>

      <div className="min-w-0">
        {/* Truncated, never sized to its content: a document name is
            user-supplied and unbounded. */}
        <p className="truncate text-[13px] font-medium" title={name}>
          {name}
        </p>
        <p className="truncate font-mono text-[11px] text-muted-foreground">{mainPath}</p>
      </div>

      <Badge variant="outline" className="text-[10px] uppercase">
        {engine}
      </Badge>

      {pdfStale && (
        <Button
          variant="outline"
          size="sm"
          onClick={onCompile}
          className="gap-1.5 border-amber-500 text-amber-700 dark:text-amber-400"
        >
          <RefreshCw className="size-3.5" aria-hidden />
          Out of date — compile to sync
        </Button>
      )}

      <div className="flex w-full items-center gap-2 overflow-x-auto sm:ml-auto sm:w-auto">
        <Tabs value={viewMode} onValueChange={(v) => onViewModeChange(v as ViewMode)}>
          <TabsList>
            <TabsTrigger value="source">Source</TabsTrigger>
            <TabsTrigger value="split">Split</TabsTrigger>
            <TabsTrigger value="pdf">PDF</TabsTrigger>
          </TabsList>
        </Tabs>

        <Button
          size="sm"
          onClick={onCompile}
          disabled={!canEdit || compiling}
          title={canEdit ? "Compile (Cmd/Ctrl+S)" : "You need edit access to compile"}
          className="gap-1.5"
        >
          {compiling ? (
            <Loader2 className="size-3.5 animate-spin" aria-hidden />
          ) : (
            <Play className="size-3.5" aria-hidden />
          )}
          Compile
        </Button>

        <Button variant="outline" size="sm" onClick={onShare} className="gap-1.5">
          <Share2 className="size-3.5" aria-hidden />
          Share
        </Button>

        <DropdownMenu>
          <DropdownMenuTrigger render={<Button variant="ghost" size="icon" aria-label="More actions" />}>
            <MoreVertical className="size-4" aria-hidden />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={onRename} disabled={!canEdit}>
              Rename
            </DropdownMenuItem>
            <DropdownMenuItem
              onClick={onDownloadPdf}
              disabled={!canDownloadPdf}
              title={canDownloadPdf ? undefined : "Compile first to download a PDF"}
            >
              Download PDF
            </DropdownMenuItem>
            <DropdownMenuItem onClick={onExport}>Export .zip</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="text-[11px] font-medium text-muted-foreground" title={ENGINE_HINT}>
              Engine
            </DropdownMenuLabel>
            <DropdownMenuRadioGroup
              value={engine}
              onValueChange={(v) => {
                if (v === "pdflatex" || v === "xelatex") onEngineChange(v);
              }}
            >
              <DropdownMenuRadioItem value="pdflatex" disabled={!canEdit} title={ENGINE_HINT}>
                pdflatex
              </DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="xelatex" disabled={!canEdit} title={ENGINE_HINT}>
                xelatex
              </DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onClick={onDelete}
              disabled={!canEdit}
              className="text-destructive focus:text-destructive"
            >
              Delete
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
