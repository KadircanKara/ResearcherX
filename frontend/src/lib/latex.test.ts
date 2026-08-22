import { describe, expect, it, vi, afterEach } from "vitest";
import {
  listFiles,
  readTextFile,
  writeTextFile,
  deleteFile,
  renameFile,
  planImport,
  patchDocument,
  AmbiguousMainError,
  PathCollisionError,
  NameCollisionError,
  LatexRequestError,
  errorText,
} from "./latex";

function mockFetch(status: number, body: unknown, contentType = "application/json") {
  // Typed as `typeof fetch` so `fetchMock.mock.calls[0]` keeps fetch's real
  // [input, init] tuple rather than the `[]` vitest infers from a
  // zero-argument implementation -- the tests below index into both
  // positions to inspect the URL and the request body.
  const fn = vi.fn<typeof fetch>(async () => ({
    ok: status >= 200 && status < 300,
    status,
    headers: { get: () => contentType },
    json: async () => body,
    text: async () => JSON.stringify(body),
    arrayBuffer: async () => new ArrayBuffer(0),
  }) as unknown as Response);
  vi.stubGlobal("fetch", fn);
  return fn;
}

afterEach(() => vi.unstubAllGlobals());

describe("path encoding", () => {
  it("escapes a path with a slash into the query string, never the route", async () => {
    const fetchMock = mockFetch(200, { path: "chapters/a.tex", content: "x" });
    await readTextFile("p1", "d1", "chapters/a.tex");
    const url = String(fetchMock.mock.calls[0][0]);
    expect(url).toContain("/latex/d1/file?path=chapters%2Fa.tex");
    // The slash must not have survived raw: every proxy in the chain would
    // otherwise become a participant in the escaping scheme.
    expect(url).not.toContain("file?path=chapters/a.tex");
  });

  it("escapes a path containing a space and a plus", async () => {
    const fetchMock = mockFetch(200, { path: "a b+c.tex", content: "" });
    await readTextFile("p1", "d1", "a b+c.tex");
    expect(String(fetchMock.mock.calls[0][0])).toContain("path=a%20b%2Bc.tex");
  });
});

describe("mutations", () => {
  it("returns the revision and used_bytes the server reported", async () => {
    mockFetch(200, {
      file: { path: "a.tex", is_binary: false, size_bytes: 5, updated_at: "2026-01-01T00:00:00Z" },
      revision: 7,
      used_bytes: 512,
    });
    const res = await writeTextFile("p1", "d1", "a.tex", "hello");
    expect(res.revision).toBe(7);
    expect(res.used_bytes).toBe(512);
  });

  it("a delete returns a mutation with no file", async () => {
    mockFetch(200, { file: null, revision: 9, used_bytes: 0 });
    const res = await deleteFile("p1", "d1", "a.tex");
    expect(res.file).toBeNull();
    expect(res.revision).toBe(9);
  });

  it("a rename sends from/to under the wire names the backend aliases", async () => {
    const fetchMock = mockFetch(200, { file: null, revision: 2, used_bytes: 1 });
    await renameFile("p1", "d1", "a.tex", "b.tex");
    const init = fetchMock.mock.calls[0][1] as { body: string };
    expect(JSON.parse(init.body)).toEqual({ from: "a.tex", to: "b.tex" });
  });
});

describe("errors", () => {
  it("surfaces the server's own detail for a user error", async () => {
    mockFetch(413, { detail: "26000000 bytes exceeds the 25000000 byte limit" });
    await expect(writeTextFile("p1", "d1", "a.tex", "x")).rejects.toBeInstanceOf(
      LatexRequestError
    );
    await expect(writeTextFile("p1", "d1", "a.tex", "x")).rejects.toMatchObject({
      status: 413,
      detail: "26000000 bytes exceeds the 25000000 byte limit",
    });
  });

  it("an ambiguous main file is its own typed error carrying the candidates", async () => {
    mockFetch(422, {
      detail: { error: "ambiguous_main", candidates: ["main.tex", "paper.tex"] },
    });
    const err = await planImport("p1", new Blob(["x"]), { name: "Project" }).catch((e) => e);
    expect(err).toBeInstanceOf(AmbiguousMainError);
    expect((err as AmbiguousMainError).candidates).toEqual(["main.tex", "paper.tex"]);
  });

  it("a path collision is its own typed error carrying the collisions", async () => {
    mockFetch(409, {
      detail: {
        error: "path_collision",
        collisions: [{ path: "chapters/intro.tex", existing: "chapters/intro.tex", suggestion: "chapters/intro (1).tex" }],
      },
    });
    const err = await planImport("p1", new Blob(["x"]), { name: "Project" }).catch((e) => e);
    expect(err).toBeInstanceOf(PathCollisionError);
    expect((err as PathCollisionError).collisions).toEqual([
      { path: "chapters/intro.tex", existing: "chapters/intro.tex", suggestion: "chapters/intro (1).tex" },
    ]);
  });

  it("a name collision is its own typed error carrying the name and suggestion", async () => {
    mockFetch(409, {
      detail: { error: "name_collision", name: "Project", suggestion: "Project (1)" },
    });
    const err = await planImport("p1", new Blob(["x"]), { name: "Project" }).catch((e) => e);
    expect(err).toBeInstanceOf(NameCollisionError);
    expect((err as NameCollisionError).takenName).toBe("Project");
    expect((err as NameCollisionError).suggestion).toBe("Project (1)");
  });

  it("a document-level route surfaces the backend's own detail too", async () => {
    // The document routes went through `apiSend`, which throws a bare
    // `Error("PATCH ... -> 422")` carrying no server detail -- so "The main
    // file must be a .tex file" and every 403 behind Set-as-main and the
    // engine picker showed the generic line instead, on controls that have
    // no other feedback path. Pinned here because nothing else in the suite
    // covers the document routes' error path, and a silent regression to
    // `apiSend` would look identical from the outside.
    mockFetch(422, { detail: "The main file must be a .tex file." });
    await expect(patchDocument("p1", "d1", { main_path: "fig.png" })).rejects.toMatchObject({
      status: 422,
      detail: "The main file must be a .tex file.",
    });
  });

  it("a document-level 5xx still does not leak server text", async () => {
    mockFetch(500, { detail: "Traceback (most recent call last): ..." });
    const err = await patchDocument("p1", "d1", { engine: "xelatex" }).catch((e) => e);
    expect((err as LatexRequestError).userMessage).toBe(
      "Something went wrong. Please try again."
    );
    expect((err as LatexRequestError).userMessage).not.toContain("Traceback");
  });

  it("a 5xx does not leak the server's text to the user", async () => {
    mockFetch(500, { detail: "Traceback (most recent call last): ..." });
    const err = await listFiles("p1", "d1").catch((e) => e);
    expect((err as LatexRequestError).userMessage).toBe(
      "Something went wrong. Please try again."
    );
    expect((err as LatexRequestError).userMessage).not.toContain("Traceback");
  });
});

describe("errorText", () => {
  // The typed 4xx errors are NOT `LatexRequestError`s, so they used to fall
  // straight through to the generic line. Both production create sites route
  // a failure through `errorText`, which made "that name is taken" read as
  // "Something went wrong. Please try again." -- an unactionable dead end on
  // the one failure that has an obvious one-click answer.
  it("names the taken document name AND the server's suggestion", () => {
    const text = errorText(new NameCollisionError("Paper", "Paper (1)"));
    expect(text).toContain("Paper");
    expect(text).toContain("Paper (1)");
    expect(text).not.toBe("Something went wrong. Please try again.");
  });

  it("says what an ambiguous main file actually is, and lists the candidates", () => {
    const text = errorText(new AmbiguousMainError(["main.tex", "paper.tex"]));
    expect(text).toContain("main.tex");
    expect(text).toContain("paper.tex");
    expect(text).not.toBe("Something went wrong. Please try again.");
  });

  it("still says something useful when the candidate list is empty", () => {
    const text = errorText(new AmbiguousMainError([]));
    expect(text).toMatch(/more than one main file/i);
  });

  it("keeps a 4xx's own detail", () => {
    expect(errorText(new LatexRequestError(413, "That archive is too large."))).toBe(
      "That archive is too large."
    );
  });

  it("still refuses to leak a 5xx's text", () => {
    expect(errorText(new LatexRequestError(500, "Traceback (most recent call last): ..."))).toBe(
      "Something went wrong. Please try again."
    );
  });

  it("gives a plain Error the generic line", () => {
    expect(errorText(new TypeError("Failed to fetch"))).toBe(
      "Something went wrong. Please try again."
    );
  });
});
