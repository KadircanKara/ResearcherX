"use client";

import { useId, type Dispatch, type SetStateAction } from "react";
import { Label } from "@/components/ui/label";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { FileConflictList, NameConflictFields } from "@/components/latex/conflict-dialog";
import type { LatexImportPlan } from "@/lib/latex";
import type { ConflictState } from "@/lib/latex-conflicts";
import { nameCollisionRow, planHasQuestions } from "@/lib/latex-import";

/**
 * Step 2 of the import dialog: every question the server's plan asked, in
 * one place -- which main file (only when detection refused to guess), what
 * to call a project whose name is taken, and where each colliding file of a
 * merge should land. Every suggestion shown is the server's own.
 */
export function ImportZipPlanStep({
  plan,
  chosenMain,
  onChooseMain,
  nameState,
  setNameState,
  fileState,
  setFileState,
  takenNames,
  takenPaths,
  busy,
}: {
  plan: LatexImportPlan;
  chosenMain: string | null;
  onChooseMain: (path: string) => void;
  nameState: ConflictState;
  setNameState: Dispatch<SetStateAction<ConflictState>>;
  fileState: ConflictState;
  setFileState: Dispatch<SetStateAction<ConflictState>>;
  takenNames: string[];
  takenPaths: string[];
  busy: boolean;
}) {
  const baseId = useId();
  const nameRow = nameCollisionRow(plan);
  const candidates = plan.ambiguous_main;

  return (
    <div className="space-y-5">
      {candidates && (
        <div className="space-y-2">
          <p className="text-[13px] font-medium">Choose the main file</p>
          <p className="text-[12px] text-muted-foreground">
            That archive has more than one main file. Which one should be compiled?
          </p>
          {/* Left unselected on purpose: the question exists because the
              backend refused to guess, so defaulting to the first candidate
              would be exactly the confident wrong answer it avoided. */}
          <RadioGroup
            value={chosenMain}
            disabled={busy}
            onValueChange={(value) => onChooseMain(value as string)}
          >
            {candidates.map((path, i) => (
              <Label
                key={path}
                htmlFor={`${baseId}-main-${i}`}
                className="flex cursor-pointer items-center gap-2 rounded-md border p-2 font-mono text-[12px] has-[[data-checked]]:border-primary"
              >
                <RadioGroupItem value={path} id={`${baseId}-main-${i}`} />
                <span className="min-w-0 truncate" title={path}>
                  {path}
                </span>
              </Label>
            ))}
          </RadioGroup>
        </div>
      )}

      {nameRow && (
        <div className="space-y-2">
          <p className="text-[13px] font-medium">That name is taken</p>
          <NameConflictFields
            collision={nameRow}
            state={nameState}
            setState={setNameState}
            taken={takenNames}
            busy={busy}
          />
        </div>
      )}

      {plan.collisions.length > 0 && (
        <div className="space-y-2">
          <p className="text-[13px] font-medium">Some files already exist</p>
          <p className="text-[12px] text-muted-foreground">
            Its files are unpacked into this project. LaTeX cannot read inside an archive, so a .zip
            left whole could never be referenced.
          </p>
          <div className="max-h-64 overflow-y-auto">
            <FileConflictList
              collisions={plan.collisions}
              state={fileState}
              setState={setFileState}
              taken={takenPaths}
              busy={busy}
            />
          </div>
        </div>
      )}

      {!planHasQuestions(plan) && (
        <p className="text-[13px] text-muted-foreground">No conflicts found. Ready to import.</p>
      )}
    </div>
  );
}
