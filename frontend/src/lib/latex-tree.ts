import type { LatexFileMeta } from "./latex";

export interface TreeNode {
  /** Tree-relative path. For a directory, the directory's own path. */
  path: string;
  /** The last segment, which is what the sidebar renders. */
  name: string;
  kind: "dir" | "file";
  /** A file's own size, or the sum of everything beneath a directory. */
  size_bytes: number;
  is_binary: boolean;
  children: TreeNode[];
}

const TEXT_EXTENSIONS = new Set([
  "tex", "sty", "cls", "bib", "bbl", "txt", "md", "cfg", "clo", "bst", "log",
]);
const IMAGE_EXTENSIONS = new Set(["png", "jpg", "jpeg", "gif", "webp", "svg"]);

function extension(path: string): string {
  const dot = path.lastIndexOf(".");
  return dot === -1 ? "" : path.slice(dot + 1).toLowerCase();
}

/**
 * Editable as text.
 *
 * Deliberately an EXTENSION test and not a `!is_binary` test: the backend's
 * `is_binary` records how the bytes are STORED (a UTF-8 decode succeeded),
 * which is a different question from whether a human should be shown a
 * CodeMirror buffer for it. Both are used -- this gates the editor, and
 * `is_binary` gates the fetch.
 */
export function isTexPath(path: string): boolean {
  return TEXT_EXTENSIONS.has(extension(path));
}

/** Renderable in an <img>. A .pdf is excluded: browsers do not draw one there. */
export function isImagePath(path: string): boolean {
  return IMAGE_EXTENSIONS.has(extension(path));
}

export function parentDir(path: string): string {
  const slash = path.lastIndexOf("/");
  return slash === -1 ? "" : path.slice(0, slash);
}

export function joinPath(dir: string, name: string): string {
  return dir ? `${dir}/${name}` : name;
}

/** The last path segment -- what a tab or a download names the file as. */
export function basename(path: string): string {
  const slash = path.lastIndexOf("/");
  return slash === -1 ? path : path.slice(slash + 1);
}

/**
 * Flat paths into a nested tree.
 *
 * Directories sort before files, and each group sorts case-insensitively so
 * `Zeta.tex` does not land above `alpha.tex` the way a raw codepoint sort
 * puts every capital first.
 */
export function buildTree(files: LatexFileMeta[]): TreeNode[] {
  const root: TreeNode = {
    path: "", name: "", kind: "dir", size_bytes: 0, is_binary: false, children: [],
  };

  for (const file of files) {
    const segments = file.path.split("/");
    let node = root;
    for (let i = 0; i < segments.length - 1; i += 1) {
      const dirPath = segments.slice(0, i + 1).join("/");
      let next = node.children.find((c) => c.kind === "dir" && c.path === dirPath);
      if (!next) {
        next = {
          path: dirPath, name: segments[i], kind: "dir",
          size_bytes: 0, is_binary: false, children: [],
        };
        node.children.push(next);
      }
      node = next;
    }
    node.children.push({
      path: file.path,
      name: segments[segments.length - 1],
      kind: "file",
      size_bytes: file.size_bytes,
      is_binary: file.is_binary,
      children: [],
    });
  }

  function finish(node: TreeNode): number {
    node.children.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === "dir" ? -1 : 1;
      return a.name.localeCompare(b.name, undefined, { sensitivity: "base" });
    });
    if (node.kind === "dir") {
      node.size_bytes = node.children.reduce((sum, child) => sum + finish(child), 0);
    }
    return node.size_bytes;
  }
  finish(root);
  return root.children;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  // Round BEFORE deciding the unit, not after. Testing the raw value for
  // promotion and then rounding for display is how 1048575 bytes printed
  // "1024 KB" instead of "1 MB": 1023.999... KB fails the `>= 1024` test
  // above and is then rounded up past it anyway. Rounding first and
  // re-checking is what keeps the promotion decision and the printed number
  // talking about the same quantity.
  let rounded = Math.round(value * 10) / 10;
  if (rounded >= 1024 && unit < units.length - 1) {
    rounded = Math.round((rounded / 1024) * 10) / 10;
    unit += 1;
  }
  // The same "decide from the rounded value" rule applies to the decimal:
  // 1025 bytes is 1.0009 KB, and a trailing ".0" on it is noise.
  return `${Number.isInteger(rounded) ? rounded : rounded.toFixed(1)} ${units[unit]}`;
}
