"use client";

import { useState } from "react";
import {
  ChevronRight,
  File,
  FileCode2,
  FileImage,
  Folder,
  Pencil,
  Plus,
  Star,
  Trash2,
  Upload,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { basename, isImagePath, isTexPath, joinPath, siblingPath, type TreeNode } from "@/lib/latex-tree";

interface FileTreeProps {
  nodes: TreeNode[];
  activePath: string | null;
  mainPath: string | null;
  canEdit: boolean;
  onOpen: (path: string) => void;
  /** Open the new-file dialog seeded with this directory ("" is the root). */
  onNewFileIn: (dir: string) => void;
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
}

/**
 * The project's file tree, ported from the prototype's `LatexFileTree`.
 *
 * At rest every row is exactly the prototype's. The real engine's extra
 * actions -- set main, rename, delete, and a folder's new file / upload --
 * appear only while a row is hovered or holds focus, laid OVER the row's
 * end so the row itself keeps the prototype's box and truncation.
 */
export function FileTree({
  nodes,
  activePath,
  mainPath,
  canEdit,
  onOpen,
  onNewFileIn,
  onDelete,
  onRename,
  onRenameDir,
  onSetMain,
  onUpload,
}: FileTreeProps) {
  // Every directory starts expanded -- a LaTeX project is small (a handful
  // of chapters, a bib file, some figures) and a collapsed-by-default tree
  // just hides the file the user came here to open. Collapsing is therefore
  // tracked as the set of paths a user EXPLICITLY closed, not the set they
  // opened, so a directory freshly created by a new file's path still shows
  // its contents without this state knowing it exists yet.
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
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

  function submitRename(path: string) {
    const trimmed = renameValue.trim();
    setRenaming(null);
    if (!trimmed) return;
    // Resolved against the row's OWN directory before it leaves this
    // component, so everything downstream -- the collision dialog's retry,
    // the backend's `normalize_path` -- keeps seeing a full destination
    // path and no layer has to guess what a bare name meant. Sent otherwise
    // VERBATIM: `latex_paths.normalize_path` on the backend is the one
    // traversal guard, and a browser-side copy of its rules would drift.
    const target = siblingPath(path, trimmed);
    if (target === path) return;
    if (renamingKind === "dir") onRenameDir(path, target);
    else onRename(path, target);
  }

  const shared: RowShared = {
    activePath,
    mainPath,
    canEdit,
    collapsed,
    renaming,
    renameValue,
    onToggle: toggle,
    onOpen,
    onDelete,
    onSetMain,
    onUpload,
    onNewFileIn: (dir) => {
      // Creating inside a collapsed folder would land the new file somewhere
      // the user cannot see, so asking for one opens the folder too.
      setCollapsed((prev) => {
        const next = new Set(prev);
        next.delete(dir);
        return next;
      });
      onNewFileIn(dir);
    },
    onStartRename: (path, kind) => {
      setRenaming(path);
      setRenamingKind(kind);
      // The LEAF, not the whole path: renaming is not moving, and a user
      // editing `Figures/genetic_operators/ox.png` should type `ox2.png`
      // rather than retyping the directories back.
      setRenameValue(basename(path));
    },
    onRenameValueChange: setRenameValue,
    onSubmitRename: submitRename,
    onCancelRename: () => setRenaming(null),
  };

  return (
    <nav aria-label="Project files" className="space-y-0.5 p-1.5">
      {nodes.length === 0 && (
        <p className="px-1.5 py-1 text-[13px] text-muted-foreground">No files yet.</p>
      )}
      {nodes.map((node) => (
        <TreeRow key={node.path} node={node} depth={0} {...shared} />
      ))}
    </nav>
  );
}

interface RowShared {
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
  onNewFileIn: (dir: string) => void;
  onStartRename: (path: string, kind: "file" | "dir") => void;
  onRenameValueChange: (value: string) => void;
  onSubmitRename: (path: string) => void;
  onCancelRename: () => void;
}

function iconFor(node: TreeNode) {
  // `is_binary` as well as the extension: a latin-1 `.bib` out of a real
  // Overleaf project is STORED as binary and opens in the binary preview,
  // not the editor, so it must not wear the source icon.
  if (isTexPath(node.path) && !node.is_binary) return FileCode2;
  if (isImagePath(node.path)) return FileImage;
  return File;
}

const actionClass =
  "rounded p-0.5 text-muted-foreground hover:bg-muted-foreground/20 hover:text-foreground";

function RenameField({
  depth,
  path,
  shared,
}: {
  depth: number;
  path: string;
  shared: RowShared;
}) {
  return (
    <div className="py-0.5 pr-1.5" style={{ paddingLeft: depth * 14 + 6 }}>
      <input
        autoFocus
        aria-label={`New name for ${basename(path)}`}
        value={shared.renameValue}
        onChange={(e) => shared.onRenameValueChange(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") shared.onSubmitRename(path);
          if (e.key === "Escape") shared.onCancelRename();
        }}
        onBlur={() => shared.onSubmitRename(path)}
        className="h-7 w-full rounded-md border border-input bg-background px-1.5 text-[13px] shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
      />
    </div>
  );
}

function TreeRow({ node, depth, ...shared }: RowShared & { node: TreeNode; depth: number }) {
  if (shared.renaming === node.path) {
    return <RenameField depth={depth} path={node.path} shared={shared} />;
  }

  if (node.kind === "dir") {
    const open = !shared.collapsed.has(node.path);
    return (
      <div>
        <div className="group/row relative">
          <button
            type="button"
            onClick={() => shared.onToggle(node.path)}
            onKeyDown={(e) => {
              // Same binding as a file row -- a folder is renameable too, and
              // having Enter mean two different things depending on the row
              // type is the kind of inconsistency that makes a keyboard user
              // stop trusting the key. Space still toggles.
              if (!shared.canEdit) return;
              if (e.key === "Enter" || e.key === "F2") {
                e.preventDefault();
                shared.onStartRename(node.path, "dir");
              }
            }}
            aria-expanded={open}
            className="flex w-full items-center gap-1 rounded-md px-1.5 py-1 text-left text-[13px] text-muted-foreground hover:bg-muted"
            style={{ paddingLeft: depth * 14 + 6 }}
          >
            <ChevronRight className={cn("size-3 shrink-0 transition-transform", open && "rotate-90")} aria-hidden />
            <Folder className="size-3.5 shrink-0" aria-hidden />
            <span className="truncate">{node.name}</span>
          </button>
          {shared.canEdit && (
            <div className="absolute inset-y-0 right-0 hidden items-center gap-0.5 rounded-r-md bg-muted pl-1 pr-1 group-focus-within/row:flex group-hover/row:flex">
              <button
                type="button"
                className={actionClass}
                title="New file in this folder"
                aria-label={`New file in ${node.name}`}
                onClick={() => shared.onNewFileIn(node.path)}
              >
                <Plus className="size-3" aria-hidden />
              </button>
              <button
                type="button"
                className={actionClass}
                title="Rename folder"
                aria-label={`Rename folder ${node.name}`}
                onClick={() => shared.onStartRename(node.path, "dir")}
              >
                <Pencil className="size-3" aria-hidden />
              </button>
              <label
                className={cn(actionClass, "cursor-pointer")}
                title="Upload a file into this folder"
                aria-label={`Upload a file into ${node.name}`}
              >
                <Upload className="size-3" aria-hidden />
                <input
                  type="file"
                  className="hidden"
                  onChange={(e) => {
                    const file = e.target.files?.[0];
                    // Cleared so picking the SAME file twice still fires
                    // `change` -- re-uploading a figure you just fixed is
                    // the common case.
                    e.target.value = "";
                    if (file) shared.onUpload(joinPath(node.path, file.name), file);
                  }}
                />
              </label>
            </div>
          )}
        </div>
        {open && (
          <div>
            {node.children.map((child) => (
              <TreeRow key={child.path} node={child} depth={depth + 1} {...shared} />
            ))}
          </div>
        )}
      </div>
    );
  }

  const Icon = iconFor(node);
  const isActive = shared.activePath === node.path;
  const isMain = node.path === shared.mainPath;
  return (
    <div className="group/row relative">
      <button
        type="button"
        onClick={() => shared.onOpen(node.path)}
        onKeyDown={(e) => {
          // Enter renames, the way Finder and VS Code's explorer do. It has
          // to preventDefault because Enter on a focused <button> would
          // otherwise fire its click and re-open a file that is already
          // open. F2 does the same for anyone who learned the Windows
          // binding, and Space still activates the button, so the row keeps
          // a keyboard route to its primary action.
          if (!shared.canEdit) return;
          if (e.key === "Enter" || e.key === "F2") {
            e.preventDefault();
            shared.onStartRename(node.path, "file");
          }
        }}
        aria-current={isActive ? "true" : undefined}
        title={isMain ? `${node.path} (main file)` : node.path}
        className={cn(
          "flex w-full items-center gap-1.5 rounded-md px-1.5 py-1 text-left text-[13px] transition-colors",
          isActive ? "bg-accent text-accent-foreground" : "text-foreground hover:bg-muted"
        )}
        style={{ paddingLeft: depth * 14 + 6 }}
      >
        <Icon className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
        <span className="truncate">{node.name}</span>
      </button>
      {shared.canEdit && (
        <div className="absolute inset-y-0 right-0 hidden items-center gap-0.5 rounded-r-md bg-muted pl-1 pr-1 group-focus-within/row:flex group-hover/row:flex">
          {!isMain && (
            <button
              type="button"
              className={actionClass}
              title="Set as main file"
              aria-label={`Set ${node.name} as the main file`}
              onClick={() => shared.onSetMain(node.path)}
            >
              <Star className="size-3" aria-hidden />
            </button>
          )}
          <button
            type="button"
            className={actionClass}
            title="Rename"
            aria-label={`Rename ${node.name}`}
            onClick={() => shared.onStartRename(node.path, "file")}
          >
            <Pencil className="size-3" aria-hidden />
          </button>
          <button
            type="button"
            className={cn(actionClass, "hover:text-destructive")}
            title="Delete"
            aria-label={`Delete ${node.name}`}
            onClick={() => shared.onDelete(node.path)}
          >
            <Trash2 className="size-3" aria-hidden />
          </button>
        </div>
      )}
    </div>
  );
}
