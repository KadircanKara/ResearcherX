import { describe, expect, it } from "vitest";
import type { LatexImportPlan } from "./latex";
import { initialState, setDefault, setOverride } from "./latex-conflicts";
import { commitBody, nameCollisionRow, planHasQuestions, planReady } from "./latex-import";

function plan(overrides: Partial<LatexImportPlan> = {}): LatexImportPlan {
  return {
    staging_id: "tok",
    mode: "create",
    file_count: 3,
    collisions: [],
    name_collision: null,
    ambiguous_main: null,
    ...overrides,
  };
}

const answers = (p: LatexImportPlan) => ({
  plan: p,
  chosenMain: null as string | null,
  nameState: initialState(),
  fileState: initialState(),
  takenNames: ["Paper"],
  takenPaths: ["main.tex", "fig.png"],
});

const MERGE_COLLISIONS = [
  { path: "main.tex", existing: "main.tex", suggestion: "main (1).tex" },
  { path: "fig.png", existing: "fig.png", suggestion: "fig (1).png" },
];

describe("nameCollisionRow", () => {
  it("is null when the name is free", () => {
    expect(nameCollisionRow(plan())).toBeNull();
  });

  it("turns a duplicate name into a one-row collision carrying the server's suggestion", () => {
    const row = nameCollisionRow(
      plan({ name_collision: { name: "Paper", suggestion: "Paper (2)" } })
    );
    expect(row).toEqual({ path: "Paper", existing: "Paper", suggestion: "Paper (2)" });
  });
});

describe("planHasQuestions", () => {
  it("is false for a plan with nothing to ask", () => {
    expect(planHasQuestions(plan())).toBe(false);
  });

  it("is true for each kind of question", () => {
    expect(planHasQuestions(plan({ ambiguous_main: ["a.tex", "b.tex"] }))).toBe(true);
    expect(
      planHasQuestions(plan({ name_collision: { name: "Paper", suggestion: "Paper (2)" } }))
    ).toBe(true);
    expect(planHasQuestions(plan({ mode: "merge", collisions: MERGE_COLLISIONS }))).toBe(true);
  });
});

describe("planReady", () => {
  it("is ready when nothing was asked", () => {
    expect(planReady(answers(plan()))).toBe(true);
  });

  it("waits for a main file and never defaults one", () => {
    const a = answers(plan({ ambiguous_main: ["main.tex", "paper.tex"] }));
    expect(planReady(a)).toBe(false);
    expect(planReady({ ...a, chosenMain: "paper.tex" })).toBe(true);
  });

  it("accepts the server's name suggestion as it stands", () => {
    const a = answers(plan({ name_collision: { name: "Paper", suggestion: "Paper (2)" } }));
    expect(planReady(a)).toBe(true);
  });

  it("refuses a renamed document name that is empty or taken", () => {
    const a = answers(plan({ name_collision: { name: "Paper", suggestion: "Paper (2)" } }));
    expect(
      planReady({ ...a, nameState: setOverride(initialState(), "Paper", "rename", "  ") })
    ).toBe(false);
    expect(
      planReady({ ...a, nameState: setOverride(initialState(), "Paper", "rename", "paper") })
    ).toBe(false);
    expect(
      planReady({ ...a, nameState: setOverride(initialState(), "Paper", "rename", "Draft") })
    ).toBe(true);
  });

  it("refuses two file rows renamed onto one path", () => {
    const a = answers(plan({ mode: "merge", collisions: MERGE_COLLISIONS }));
    expect(planReady(a)).toBe(true);
    let fileState = setOverride(initialState(), "main.tex", "rename", "x.tex");
    fileState = setOverride(fileState, "fig.png", "rename", "x.tex");
    expect(planReady({ ...a, fileState })).toBe(false);
  });
});

describe("commitBody", () => {
  it("sends the typed name for a create with a free name", () => {
    expect(
      commitBody({ ...answers(plan()), typedName: "  My paper  " })
    ).toEqual({ staging_id: "tok", name: "My paper", decisions: [] });
  });

  it("sends the answered name, never as a file decision", () => {
    const p = plan({ name_collision: { name: "Paper", suggestion: "Paper (2)" } });
    const keep = commitBody({ ...answers(p), typedName: "Paper" });
    expect(keep).toEqual({ staging_id: "tok", name: "Paper (2)", decisions: [] });

    const renamed = commitBody({
      ...answers(p),
      nameState: setOverride(setDefault(initialState(), "rename"), "Paper", "rename", " Draft "),
      typedName: "Paper",
    });
    expect(renamed.name).toBe("Draft");
    expect(renamed.decisions).toEqual([]);
  });

  it("sends the chosen main file only when the plan asked for one", () => {
    const asked = plan({ ambiguous_main: ["main.tex", "paper.tex"] });
    expect(
      commitBody({ ...answers(asked), chosenMain: "paper.tex", typedName: "X" }).main_path
    ).toBe("paper.tex");
    expect(
      commitBody({ ...answers(plan()), chosenMain: "paper.tex", typedName: "X" }).main_path
    ).toBeUndefined();
  });

  it("sends a merge's decisions and its document, and no name", () => {
    const p = plan({ mode: "merge", collisions: MERGE_COLLISIONS });
    const body = commitBody({
      ...answers(p),
      fileState: setOverride(initialState(), "fig.png", "rename", "figures/fig.png"),
      typedName: "ignored",
      documentId: "doc-1",
    });
    expect(body).toEqual({
      staging_id: "tok",
      document_id: "doc-1",
      decisions: [
        { path: "main.tex", new_path: "main (1).tex" },
        { path: "fig.png", new_path: "figures/fig.png" },
      ],
    });
  });
});
