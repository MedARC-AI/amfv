import { useQuery } from "@tanstack/react-query"
import {
  createFileRoute,
  Link,
  Outlet,
  useRouterState,
} from "@tanstack/react-router"
import { Loader2, SplitSquareVertical } from "lucide-react"

import { homeSummaryQueryOptions } from "@/lib/queries"

export const Route = createFileRoute("/_layout/create")({
  component: Create,
  head: () => ({
    meta: [
      {
        title: "Create - AMFV Web",
      },
    ],
  }),
})

function Create() {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const summaryQuery = useQuery({
    ...homeSummaryQueryOptions,
  })

  if (pathname.replace(/\/$/, "") === "/create/retrieval") {
    return (
      <p>
        Retrieval creation is temporarily unavailable.{" "}
        <Link className="underline" to="/create/fact-decomposition">
          Create fact decomposition
        </Link>
      </p>
    )
  }

  if (pathname !== "/create") {
    return <Outlet />
  }

  const counts = summaryQuery.data?.outstanding_counts ?? {}

  return (
    <div className="flex flex-col gap-6">
      {summaryQuery.isError ? (
        <p className="text-destructive text-sm">
          Could not load authoring counts.
        </p>
      ) : null}
      {summaryQuery.isLoading ? (
        <Loader2 className="text-muted-foreground size-5 animate-spin" />
      ) : null}
      <div className="grid gap-3 sm:grid-cols-3">
        {[
          ["Drafts", countValue(counts, "draft_items")],
          ["Submitted", countValue(counts, "submitted_items")],
          ["Authored", String(summaryQuery.data?.authored_total ?? 0)],
        ].map(([label, value]) => (
          <div key={label} className="rounded-md border p-4">
            <div className="text-2xl font-semibold">{value}</div>
            <div className="text-muted-foreground mt-1 text-sm">{label}</div>
          </div>
        ))}
      </div>
      <div className="grid gap-4">
        <Link className="rounded-md border p-5" to="/create/fact-decomposition">
          <SplitSquareVertical className="text-muted-foreground size-5" />
          <h2 className="mt-4 text-base font-semibold tracking-normal">
            Fact Decomposition
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            Statement and ordered facts
          </p>
        </Link>
      </div>
    </div>
  )
}

function countValue(counts: Record<string, number>, key: string): string {
  return String(counts[key] ?? 0)
}
