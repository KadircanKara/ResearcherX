import type { LatexCollision, LatexImportPlan } from "./latex";
import { type ConflictState, decisions, problems, resolvedPath } from "./latex-conflicts";

/**
 * The import dialog's plan step, as data: which questions a staged plan asks,
 * whether every one is answered, and the commit body the answers produce.
 *
 * PURE -- no React, no fetch -- for the usual reason: vitest here runs in the
 * node environment, and every silent data-loss bug in the LaTeX editor has
 * come from logic that lived inside a component. Nothing here computes a
 * `(n)` suggestion or validates a path: the suggestions are the server's
 * (`latex_dedupe`), the path rules are `latex_paths.normalize_path`'s, and
 * `problems` below is `latex-conflicts`' advisory check, never a validator.
 */

/**
 * A duplicate document NAME as a one-row collision, so it is answered by the
 * same Keep both / Rename control and the same `ConflictState` a colliding
 * file is. `null` when the plan's name is free (always, for a merge).
 */
export function nameCollisionRow(plan: LatexImportPlan): LatexCollision | null {
  if (!plan.name_collision) return null;
  return {
    path: plan.name_collision.name,
    existing: plan.name_collision.name,
    suggestion: plan.name_collision.suggestion,
  };
}

/** Whether the plan asks anything at all. */
export function planHasQuestions(plan: LatexImportPlan): boolean {
  return (
    plan.ambiguous_main !== null ||
    plan.name_collision !== null ||
    plan.collisions.length > 0
  );
}

export interface PlanAnswers {
  plan: LatexImportPlan;
  /** The main file picked from `ambiguous_main`. Never defaulted: the
   * backend refused to guess, so neither does the client. */
  chosenMain: string | null;
  /** The answer to the duplicate-name row (`nameCollisionRow`). */
  nameState: ConflictState;
  /** The answers to the plan's file collisions (a merge). */
  fileState: ConflictState;
  /** Existing document names -- what a create's name must not collide with. */
  takenNames: string[];
  /** The open document's paths -- what a merge's decisions must not collide with. */
  takenPaths: string[];
}

/** Every question the plan asked has an answer the advisory check accepts. */
export function planReady(a: PlanAnswers): boolean {
  if (a.plan.ambiguous_main !== null && !a.chosenMain) return false;
  const nameRow = nameCollisionRow(a.plan);
  if (nameRow && Object.keys(problems(a.nameState, [nameRow], a.takenNames)).length > 0) {
    return false;
  }
  if (Object.keys(problems(a.fileState, a.plan.collisions, a.takenPaths)).length > 0) {
    return false;
  }
  return true;
}

export interface CommitBody {
  staging_id: string;
  name?: string;
  main_path?: string;
  document_id?: string;
  decisions: { path: string; new_path: string }[];
}

/**
 * What `commitImport` is sent for these answers.
 *
 * The name row is NEVER sent as a decision: the commit route checks every
 * decision's `path` against the archive's own entries and 422s on one that
 * is not there, and a document name is not an entry. It travels as `name`.
 */
export function commitBody(
  a: Pick<PlanAnswers, "plan" | "chosenMain" | "nameState" | "fileState"> & {
    /** The name typed in the drop step (a create). */
    typedName: string;
    /** The document a merge writes into. */
    documentId?: string;
  }
): CommitBody {
  const { plan } = a;
  const nameRow = nameCollisionRow(plan);
  const body: CommitBody = {
    staging_id: plan.staging_id,
    decisions: decisions(a.fileState, plan.collisions),
  };
  if (plan.mode === "create") {
    body.name = nameRow ? resolvedPath(a.nameState, nameRow) : a.typedName.trim();
  } else if (a.documentId) {
    body.document_id = a.documentId;
  }
  if (plan.ambiguous_main !== null && a.chosenMain) body.main_path = a.chosenMain;
  return body;
}
