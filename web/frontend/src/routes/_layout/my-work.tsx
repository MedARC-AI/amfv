import { useQuery } from "@tanstack/react-query"
import { createFileRoute } from "@tanstack/react-router"
import { Loader2 } from "lucide-react"

import { homeSummaryQueryOptions } from "@/lib/queries"

export const Route = createFileRoute("/_layout/my-work")({
  component: MyWork,
  head: () => ({
    meta: [
      {
        title: "My Work - AMFV Web",
      },
    ],
  }),
})

function MyWork() {
  const summaryQuery = useQuery({
    ...homeSummaryQueryOptions,
  })
  const summary = summaryQuery.data
  const counts = summary?.outstanding_counts ?? {}
  const rows = [
    ["Drafts", countValue(counts, "draft_items")],
    ["Submitted", countValue(counts, "submitted_items")],
    ["Reviewed", String(summary?.reviewed_total ?? 0)],
    ["Authored", String(summary?.authored_total ?? 0)],
  ]

  return (
    <div className="flex flex-col gap-6">
      {summaryQuery.isError ? (
        <p className="text-destructive text-sm">
          Could not load your work summary.
        </p>
      ) : null}
      {summaryQuery.isLoading ? (
        <Loader2 className="text-muted-foreground size-5 animate-spin" />
      ) : null}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {rows.map(([label, value]) => (
          <div key={label} className="rounded-md border p-4">
            <div className="text-2xl font-semibold">{value}</div>
            <div className="text-muted-foreground mt-1 text-sm">{label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}

function countValue(counts: Record<string, number>, key: string): string {
  return String(counts[key] ?? 0)
}
