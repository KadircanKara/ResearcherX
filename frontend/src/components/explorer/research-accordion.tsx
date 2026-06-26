"use client";

import { useState } from "react";
import {
  CheckCircle2,
  ChevronRight,
  Circle,
  Loader2,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

type StepStatus = "done" | "in-progress" | "pending";

type SourcePill = { label: string; className: string };

interface SubItem {
  text: string;
  pill?: SourcePill;
}

interface Step {
  title: string;
  status: StepStatus;
  badge?: string;
  items: SubItem[];
  defaultOpen: boolean;
}

const SOURCE_PILLS: Record<"arxiv" | "s2", SourcePill> = {
  arxiv: {
    label: "arXiv",
    className: "bg-red-100 text-red-700 dark:bg-red-950/60 dark:text-red-300",
  },
  s2: {
    label: "Semantic Scholar",
    className:
      "bg-cyan-100 text-cyan-700 dark:bg-cyan-950/60 dark:text-cyan-300",
  },
};

const STEPS: Step[] = [
  {
    title: "Planned 3 sub-queries",
    status: "done",
    defaultOpen: true,
    items: [
      { text: "Decentralized control & consensus" },
      { text: "Communication-efficient MARL" },
      { text: "Value decomposition scaling (QMIX past 30 agents)" },
    ],
  },
  {
    title: "Searched paper databases",
    status: "done",
    badge: "21 found",
    defaultOpen: true,
    items: [
      { text: "12 results from arXiv", pill: SOURCE_PILLS.arxiv },
      { text: "9 results from Semantic Scholar", pill: SOURCE_PILLS.s2 },
    ],
  },
  {
    title: "Ranking by relevance against your projects",
    status: "in-progress",
    defaultOpen: true,
    items: [{ text: "Scoring 21 papers vs Multi-UAV Coordination" }],
  },
  {
    title: "Draft a cited summary",
    status: "pending",
    defaultOpen: false,
    items: [
      {
        text: "Synthesise the top matches into an answer with inline citations.",
      },
    ],
  },
];

const STATUS_ICON: Record<
  StepStatus,
  { Icon: LucideIcon; className: string }
> = {
  done: { Icon: CheckCircle2, className: "size-[18px] text-emerald-500" },
  "in-progress": {
    Icon: Loader2,
    className: "size-[18px] text-primary animate-spin",
  },
  pending: { Icon: Circle, className: "size-[18px] text-muted-foreground" },
};

const STATUS_BADGE: Record<StepStatus, string> = {
  done: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  "in-progress": "bg-accent text-accent-foreground",
  pending: "bg-muted text-muted-foreground",
};

const STATUS_LABEL: Record<StepStatus, string> = {
  done: "done",
  "in-progress": "running",
  pending: "queued",
};

export function ResearchAccordion() {
  const [open, setOpen] = useState<boolean[]>(STEPS.map((s) => s.defaultOpen));

  const toggle = (i: number) =>
    setOpen((prev) => prev.map((v, idx) => (idx === i ? !v : v)));

  return (
    <div className="flex flex-col gap-2">
      {STEPS.map((step, i) => {
        const isOpen = open[i];
        const { Icon, className: iconClass } = STATUS_ICON[step.status];
        return (
          <div
            key={step.title}
            className="rounded-lg border border-border bg-card"
          >
            <button
              type="button"
              onClick={() => toggle(i)}
              aria-expanded={isOpen}
              className="flex w-full items-center gap-3 px-4 py-3 text-left"
            >
              <Icon className={iconClass} />
              <span className="flex-1 text-sm font-medium">{step.title}</span>
              <span
                className={cn(
                  "rounded-full px-2.5 py-0.5 text-[11px] font-mono",
                  STATUS_BADGE[step.status]
                )}
              >
                {step.badge ?? STATUS_LABEL[step.status]}
              </span>
              <ChevronRight
                className={cn(
                  "size-4 text-muted-foreground transition-transform",
                  isOpen && "rotate-90"
                )}
              />
            </button>

            {isOpen && (
              <div className="px-4 pb-3.5 pl-[45px] text-sm">
                <ul className="flex flex-col gap-1.5">
                  {step.items.map((item) => (
                    <li
                      key={item.text}
                      className="flex items-center gap-2 text-muted-foreground"
                    >
                      <span>{item.text}</span>
                      {item.pill && (
                        <span
                          className={cn(
                            "rounded px-2 py-0.5 text-[11px] font-mono",
                            item.pill.className
                          )}
                        >
                          {item.pill.label}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
