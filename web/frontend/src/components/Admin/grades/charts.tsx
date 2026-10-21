import { Fragment } from "react"

import { cn } from "@/lib/utils"
import { LABELS, type Label, percent, type TaskSummary } from "./gradesModel"

/** Which part of a split bar a bullet refers to. */
type AgreementTone = "all" | "some" | "single"

/** Solid for "all", striped for "some", gray for a single reviewer. */
const TONE_CLASS: Record<AgreementTone, string> = {
  all: "bg-primary",
  some: "bg-primary/20 [background-image:repeating-linear-gradient(45deg,var(--primary)_0_3px,transparent_3px_7px)]",
  single: "bg-muted-foreground/60",
}

/** A small color square that ties a bullet to its segment of the bar. */
export function Swatch({ tone }: { tone: AgreementTone }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "mr-1.5 inline-block size-3 rounded-sm border border-foreground/40 align-middle",
        TONE_CLASS[tone],
      )}
    />
  )
}

type BarSegment = { tone: AgreementTone; count: number; label: string }

/**
 * One thin horizontal bar. Each segment takes its share of `total`; when the
 * segments add up to less than the total, the rest of the bar stays empty.
 */
export function SplitBar({
  segments,
  total,
  className,
}: {
  segments: BarSegment[]
  total: number
  className?: string
}) {
  if (total === 0) return null
  return (
    <div
      aria-hidden="true"
      className={cn(
        "flex h-3.5 w-full overflow-hidden rounded-full border border-foreground/40 bg-background [&>*+*]:border-l [&>*+*]:border-background",
        className,
      )}
    >
      {segments
        .filter((segment) => segment.count > 0)
        .map((segment) => (
          <div
            className={cn("shrink-0", TONE_CLASS[segment.tone])}
            key={segment.tone}
            style={{ width: `${(segment.count / total) * 100}%` }}
            title={`${segment.count} ${segment.label}`}
          />
        ))}
    </div>
  )
}

/**
 * The bar under a Human agreement heading: claims every reviewer acted on,
 * claims only some reviewers acted on, and claims with a single reviewer.
 */
export function AgreementBar({
  all,
  some,
  single,
}: {
  all: number
  some: number
  single: number
}) {
  return (
    <SplitBar
      className="max-w-md"
      segments={[
        { tone: "all", count: all, label: "by all of their reviewers" },
        { tone: "some", count: some, label: "by only some of their reviewers" },
        { tone: "single", count: single, label: "with a single reviewer" },
      ]}
      total={all + some + single}
    />
  )
}

/** Grades counted by the model's grade (row) and the human's grade (column). */
type GradeGridCounts = Record<Label, Record<Label, number>>

export function emptyGradeGrid(): GradeGridCounts {
  return Object.fromEntries(
    LABELS.map((from) => [
      from,
      Object.fromEntries(LABELS.map((to) => [to, 0])),
    ]),
  ) as GradeGridCounts
}

/**
 * A 3 by 3 grid of grade counts. Cells on the diagonal are agreement; the
 * others are where a human moved the grade. Each cell is shaded by its count
 * and shows its share of the row.
 */
export function GradeGrid({ counts }: { counts: GradeGridCounts }) {
  const max = Math.max(
    1,
    ...LABELS.flatMap((from) => LABELS.map((to) => counts[from][to])),
  )
  const rowTotal = (from: Label) =>
    LABELS.reduce((sum, to) => sum + counts[from][to], 0)
  return (
    <div className="flex flex-col gap-2">
      <div className="grid w-fit grid-cols-[auto_repeat(3,7rem)_auto] gap-1 text-xs">
        <div />
        <div className="col-span-3 text-center text-muted-foreground">
          Human's grade
        </div>
        <div />
        <div className="self-end pr-3 text-muted-foreground">Model's grade</div>
        {LABELS.map((to) => (
          <div className="self-end pb-1 text-center font-medium" key={to}>
            {to}
          </div>
        ))}
        <div className="w-20 self-end pb-1 pl-3 text-muted-foreground">
          total claims assessed
        </div>
        {LABELS.map((from) => (
          <Fragment key={from}>
            <div className="self-center pr-3 font-medium">{from}</div>
            {LABELS.map((to) => {
              const count = counts[from][to]
              const agree = from === to
              return (
                <div
                  className={cn(
                    "relative flex h-14 flex-col items-center justify-center rounded-md border",
                    agree && "border-foreground/50",
                  )}
                  key={to}
                  title={`${count} grades: the model said ${from}, a human said ${to}`}
                >
                  <div
                    className={cn(
                      "absolute inset-0 rounded-md",
                      agree ? "bg-muted-foreground" : "bg-primary",
                    )}
                    style={{ opacity: (count / max) * 0.8 }}
                  />
                  <span className="relative font-mono text-sm font-semibold">
                    {count}
                  </span>
                  <span className="relative font-mono text-[11px] text-foreground/75">
                    {percent(count, rowTotal(from))}
                  </span>
                </div>
              )
            })}
            <div className="self-center pl-3 font-mono text-muted-foreground">
              {rowTotal(from)}
            </div>
          </Fragment>
        ))}
      </div>
      <p className="text-muted-foreground text-xs">
        Each count is one human grade on one claim (a claim graded by three
        people counts three times). Rows are the model's grade, columns the
        grade a human gave. The outlined diagonal is agreement. Below the
        diagonal the human graded up (toward vital); above it, down. Percentages
        are of the row.
      </p>
    </div>
  )
}

/**
 * One bar per reviewed task, tallest first: the share of the task's reviewed
 * claims that got a given flag. Hover names the task; click opens it.
 */
export function TaskBars({
  tasks,
  count,
  what,
  onOpenTask,
}: {
  tasks: TaskSummary[]
  /** How many of the task's reviewed claims got the flag */
  count: (task: TaskSummary) => number
  /** Finishes "N of M claims …", e.g. "flagged as a Duplicate" */
  what: string
  onOpenTask?: (caseId: string) => void
}) {
  const bars = tasks
    .filter((task) => task.judged > 0)
    .map((task) => {
      const flagged = count(task)
      return { task, flagged, share: flagged / task.judged }
    })
    .sort((a, b) => b.share - a.share || b.flagged - a.flagged)
  if (bars.length === 0) return null
  const affected = bars.filter((bar) => bar.flagged > 0).length
  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex h-20 items-end gap-[3px] border-b">
        {bars.map((bar) => {
          const label = `Task ${bar.task.taskId ?? bar.task.caseId}: ${bar.flagged} of ${bar.task.judged} claims ${what}`
          return (
            <button
              aria-label={label}
              className={cn(
                "w-full min-w-1 max-w-6 rounded-t-sm",
                bar.flagged > 0
                  ? "bg-primary hover:bg-primary/75"
                  : "bg-muted-foreground/25 hover:bg-muted-foreground/40",
                onOpenTask ? "cursor-pointer" : "cursor-default",
              )}
              key={bar.task.caseId}
              onClick={
                onOpenTask ? () => onOpenTask(bar.task.caseId) : undefined
              }
              style={{ height: `${Math.max(bar.share * 100, 4)}%` }}
              title={label}
              type="button"
            />
          )
        })}
      </div>
      <p className="text-muted-foreground text-xs">
        One bar per reviewed task, tallest first: the share of its reviewed
        claims {what}. {affected} of {bars.length} tasks have at least one.
        Hover for the task, click to open it.
      </p>
    </div>
  )
}
