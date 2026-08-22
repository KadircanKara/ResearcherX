"use client";

import { useState } from "react";
import {
  ChevronDown,
  ChevronRight,
  CloudUpload,
  FileCode,
  Folder,
  PanelLeftClose,
  Pencil,
  Plus,
  Star,
  Trash2,
  Upload,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { basename, formatBytes, joinPath, siblingPath, type TreeNode } from "@/lib/latex-tree";

interface FileTreeProps {
  nodes: TreeNode[];
  /** Owned by the workspace, not by this component: the drag handle that
   * sets it lives OUTSIDE the tree's own border (it is the seam between the
   * tree and the editor), so the two would fight over the value if the tree
   * kept its own copy. */
  width: number;
  activePath: string | null;
  mainPath: string | null;
  canEdit: boolean;
  usedBytes: number;
  maxBytes: number;
  error: string | null;
  onOpen: (path: string) => void;
  onCreate: (path: string) => void;
  onDelete: (path: string) => void;
  onRename: (from: string, to: string) => void;
  /**
   * Move a whole directory. Separate from `onRename` because a directory is
   * not a row -- it is the shared prefix of the files under it -- so the
   * backend moves it with a different, all-or-nothing route.
   */
  onRenameDir: (from: string, to: string) => void;
  onSetMain: (path: string) => void;
  onUpload: (path: string, data: Blob) => void;
  /**
   * One file the user picked from the header's "Add files" control.
   *
   * The tree reports the File and decides nothing: whether a `.zip` is
   * unpacked into this project or a `.png` is written straight into the
   * tree is policy, and policy lives with the caller that owns both paths.
   */
  onAddFile: (file: File) => void;
  onCollapse: () => void;
}

export function FileTree({
  nodes,
  width,
  activePath,
  mainPath,
  canEdit,
  usedBytes,
  maxBytes,
  error,
  onOpen,
  onCreate,
  onDelete,
  onRename,
  onRenameDir,
  onSetMain,
  onUpload,
  onAddFile,
  onCollapse,
}: FileTreeProps) {
  // Every directory starts expanded -- a LaTeX project is small (a handful
  // of chapters, a bib file, some figures) and a collapsed-by-default tree
  // just hides the file the user came here to open. Collapsing is therefore
  // tracked as the set of paths a user EXPLICITLY closed, not the set they
  // opened, so a directory freshly created by a new file's path still shows
  // its contents without this state knowing it exists yet.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [creating, setCreating] = useState(false);
  const [newPath, setNewPath] = useState("");
  // The path being renamed, and whether it names a file or a directory --
  // the two go to different routes, and the row that opened the field is
  // the only thing that knows which.
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renamingKind, setRenamingKind] = useState<"file" | "dir">("file");
  const [renameValue, setRenameValue] = useState("");

  function toggle(path: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(path)) next.delete(path);
      else next.add(path);
      return next;
    });
  }

  function submitCreate() {
    const trimmed = newPath.trim();
    setCreating(false);
    setNewPath("");
    // Sent VERBATIM, not sanitized here: `latex_paths.normalize_path` on the
    // backend is the one traversal guard, and a browser-side copy of its
    // rules would drift from it silently -- see this component's brief.
    if (trimmed) onCreate(trimmed);
  }

  function submitRename(path: string) {
    const trimmed = renameValue.trim();
    setRenaming(null);
    if (!trimmed) return;
    // Resolved against the row's OWN directory before it leaves this
    // component, so everything downstream -- the collision dialog's retry,
    // the backend's `normalize_path` -- keeps seeing a full destination
    // path and no layer has to guess what a bare name meant.
    const target = siblingPath(path, trimmed);
    if (target === path) return;
    if (renamingKind === "dir") onRenameDir(path, target);
    else onRename(path, target);
  }

  const pctUsed = maxBytes > 0 ? (usedBytes / maxBytes) * 100 : 0;

  return (
    <div
      style={{ width }}
      className="flex min-w-0 shrink-0 flex-col border-r border-border"
    >
      <div className="flex items-center justify-between gap-1 px-3 pb-1 pt-2">
        <span className="truncate text-xs font-medium uppercase tracking-wide text-muted-foreground">
          Files
        </span>
        <div className="flex shrink-0 items-center gap-0.5">
          <Button
            size="icon-sm"
            variant="ghost"
            title="Hide file tree"
            aria-label="Hide file tree"
            onClick={onCollapse}
          >
            <PanelLeftClose className="size-3.5" />
          </Button>
          {/* A label wrapping a hidden input, not a Button that opens a
              dialog: the dialog's only job would be to show a "browse"
              link, so it stood between the user and the file picker for
              nothing. Accepts ANY type -- a project needs figures, .bib,
              .sty and .cls at least as often as it needs an archive. */}
          {canEdit && (
            <label
              className="inline-flex size-7 cursor-pointer items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              title="Add files (a .zip is unpacked into this project)"
            >
              <CloudUpload className="size-3.5" />
              <input
                type="file"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  // Cleared so picking the SAME file twice still fires
                  // `change` -- re-uploading a figure you just fixed is the
                  // common case, and without this the second pick is
                  // silently ignored.
                  e.target.value = "";
                  if (file) onAddFile(file);
                }}
              />
            </label>
          )}
          <Button
            size="icon-sm"
            variant="ghost"
            disabled={!canEdit}
            title={canEdit ? "New file" : "You need editor access to add a file"}
            onClick={() => setCreating(true)}
          >
            <Plus className="size-3.5" />
          </Button>
        </div>
      </div>

      {error && <p className="px-3 pb-1 text-xs text-destructive">{error}</p>}

      {creating && (
        <input
          autoFocus
          value={newPath}
          onChange={(e) => setNewPath(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submitCreate();
            if (e.key === "Escape") {
              setCreating(false);
              setNewPath("");
            }
          }}
          onBlur={submitCreate}
          placeholder="chapters/intro.tex"
          className="mx-3 mb-1 rounded-md border border-input bg-background px-2 py-1 text-sm"
        />
      )}

      <div className="flex-1 overflow-auto px-1.5 pb-2">
        {nodes.length === 0 && !creating && (
          <p className="px-1.5 py-2 text-sm text-muted-foreground">No files yet.</p>
        )}
        {nodes.map((node) => (
          <TreeRow
            key={node.path}
            node={node}
            depth={0}
            activePath={activePath}
            mainPath={mainPath}
            canEdit={canEdit}
            collapsed={collapsed}
            renaming={renaming}
            renameValue={renameValue}
            onToggle={toggle}
            onOpen={onOpen}
            onDelete={onDelete}
            onSetMain={onSetMain}
            onUpload={onUpload}
            onStartRename={(path, kind) => {
              setRenaming(path);
              setRenamingKind(kind);
              // The LEAF, not the whole path: renaming is not moving, and a
              // user editing `Figures/genetic_operators/ox.png` should type
              // `ox2.png` rather than retyping the directories back.
              setRenameValue(basename(path));
            }}
            onRenameValueChange={setRenameValue}
            onSubmitRename={submitRename}
            onCancelRename={() => setRenaming(null)}
          />
        ))}
      </div>

      {/*
        Always visible, not just above some threshold: a cap the user can't
        see is a cap they hit as an unexplained failure -- this footer is
        the reason the tree endpoint returns these two numbers at all.
      */}
      <div className="border-t border-border px-3 py-2">
        <div className="mb-1 h-1.5 w-full overflow-hidden rounded-full bg-muted">
          <div
            className={cn(
              "h-full rounded-full",
              pctUsed >= 90 ? "bg-destructive" : "bg-primary"
            )}
            style={{ width: `${Math.min(pctUsed, 100)}%` }}
          />
        </div>
        <p
          className={cn(
            "text-xs",
            pctUsed >= 90 ? "font-medium text-destructive" : "text-muted-foreground"
          )}
        >
          {formatBytes(usedBytes)} of {formatBytes(maxBytes)}
        </p>
      </div>
    </div>
  );
}

interface TreeRowProps {
  node: TreeNode;
  depth: number;
  activePath: string | null;
  mainPath: string | null;
  canEdit: boolean;
  collapsed: Set<string>;
  renaming: string | null;
  renameValue: string;
  onToggle: (path: string) => void;
  onOpen: (path: string) => void;
  onDelete: (path: string) => void;
  onSetMain: (path: string) => void;
  onUpload: (path: string, data: Blob) => void;
  onStartRename: (path: string, kind: "file" | "dir") => void;
  onRenameValueChange: (value: string) => void;
  onSubmitRename: (path: string) => void;
  onCancelRename: () => void;
}

function TreeRow({
  node,
  depth,
  activePath,
  mainPath,
  canEdit,
  collapsed,
  renaming,
  renameValue,
  onToggle,
  onOpen,
  onDelete,
  onSetMain,
  onUpload,
  onStartRename,
  onRenameValueChange,
  onSubmitRename,
  onCancelRename,
}: TreeRowProps) {
  const indent = { paddingLeft: `${depth * 14 + 6}px` };

  if (node.kind === "dir") {
    const isCollapsed = collapsed.has(node.path);
    if (renaming === node.path) {
      return (
        <div style={indent} className="py-0.5 pr-1.5">
          <input
            autoFocus
            value={renameValue}
            onChange={(e) => onRenameValueChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onSubmitRename(node.path);
              if (e.key === "Escape") onCancelRename();
            }}
            onBlur={() => onSubmitRename(node.path)}
            className="w-full rounded-md border border-input bg-background px-1.5 py-0.5 text-sm"
          />
        </div>
      );
    }
    return (
      <div>
        <div
          className="group flex items-center gap-1 rounded-md py-1 pr-1.5 text-sm text-muted-foreground hover:bg-muted/60"
          style={indent}
        >
          <button
            className="flex min-w-0 flex-1 items-center gap-1 text-left"
            onClick={() => onToggle(node.path)}
          >
            {isCollapsed ? (
              <ChevronRight className="size-3.5 shrink-0" />
            ) : (
              <ChevronDown className="size-3.5 shrink-0" />
            )}
            <Folder className="size-3.5 shrink-0" />
            <span className="truncate">{node.name}</span>
          </button>
          {canEdit && (
            <button
              className="invisible shrink-0 text-muted-foreground hover:text-foreground group-hover:visible"
              title="Rename folder"
              aria-label={`Rename folder ${node.name}`}
              onClick={() => onStartRename(node.path, "dir")}
            >
              <Pencil className="size-3.5" />
            </button>
          )}
          {canEdit && (
            <label
              className="invisible shrink-0 cursor-pointer text-muted-foreground hover:text-foreground group-hover:visible"
              title="Upload file into this directory"
            >
              <Upload className="size-3.5" />
              <input
                type="file"
                className="hidden"
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  e.target.value = "";
                  if (file) onUpload(joinPath(node.path, file.name), file);
                }}
              />
            </label>
          )}
        </div>
        {!isCollapsed &&
          node.children.map((child) => (
            <TreeRow
              key={child.path}
              node={child}
              depth={depth + 1}
              activePath={activePath}
              mainPath={mainPath}
              canEdit={canEdit}
              collapsed={collapsed}
              renaming={renaming}
              renameValue={renameValue}
              onToggle={onToggle}
              onOpen={onOpen}
              onDelete={onDelete}
              onSetMain={onSetMain}
              onUpload={onUpload}
              onStartRename={onStartRename}
              onRenameValueChange={onRenameValueChange}
              onSubmitRename={onSubmitRename}
              onCancelRename={onCancelRename}
            />
          ))}
      </div>
    );
  }

  const isMain = node.path === mainPath;

  if (renaming === node.path) {
    return (
      <div style={indent} className="py-0.5 pr-1.5">
        <input
          autoFocus
          value={renameValue}
          onChange={(e) => onRenameValueChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") onSubmitRename(node.path);
            if (e.key === "Escape") onCancelRename();
          }}
          onBlur={() => onSubmitRename(node.path)}
          className="w-full rounded-md border border-input bg-background px-1.5 py-0.5 text-sm"
        />
      </div>
    );
  }

  return (
    <div
      className={cn(
        "group flex items-center gap-1 rounded-md py-1 pr-1.5 text-sm",
        node.path === activePath
          ? "bg-muted font-medium text-foreground"
          : "text-muted-foreground hover:bg-muted/60 hover:text-foreground"
      )}
      style={indent}
    >
      <button
        className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
        onClick={() => onOpen(node.path)}
      >
        <FileCode className="size-3.5 shrink-0" />
        <span className="truncate">{node.name}</span>
        {isMain && (
          <span className="shrink-0 rounded-full bg-primary/10 px-1.5 py-px text-[10px] font-medium text-primary">
            main
          </span>
        )}
      </button>
      {canEdit && (
        <div className="invisible flex shrink-0 items-center gap-0.5 group-hover:visible">
          {!isMain && (
            <button
              title="Set as main"
              className="text-muted-foreground hover:text-foreground"
              onClick={() => onSetMain(node.path)}
            >
              <Star className="size-3.5" />
            </button>
          )}
          <button
            title="Rename"
            className="text-muted-foreground hover:text-foreground"
            onClick={() => onStartRename(node.path, "file")}
          >
            <Pencil className="size-3.5" />
          </button>
          <button
            title="Delete"
            className="text-muted-foreground hover:text-destructive"
            onClick={() => onDelete(node.path)}
          >
            <Trash2 className="size-3.5" />
          </button>
        </div>
      )}
    </div>
  );
}
