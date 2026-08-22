import { useQuery } from "@tanstack/react-query"
import {
  createFileRoute,
  Link,
  Outlet,
  useRouterState,
} from "@tanstack/react-router"
import { FileSearch, ListChecks, Loader2, SearchCheck } from "lucide-react"

import { homeSummaryQueryOptions } from "@/lib/queries"

export const Route = createFileRoute("/_layout/review")({
  component: Review,
  head: () => ({
    meta: [
      {
        title: "Review - AMFV Web",
      },
    ],
  }),
})

function Review() {
  const pathname = useRouterState({
    select: (state) => state.location.pathname,
  })
  const summaryQuery = useQuery({
    ...homeSummaryQueryOptions,
  })

  if (pathname !== "/review") {
    return <Outlet />
  }

  const counts = summaryQuery.data?.outstanding_counts ?? {}

  return (
    <div className="flex flex-col gap-6">
      {summaryQuery.isError ? (
        <p className="text-destructive text-sm">
          Could not load review counts.
        </p>
      ) : null}
      {summaryQuery.isLoading ? (
        <Loader2 className="text-muted-foreground size-5 animate-spin" />
      ) : null}
      <div className="grid gap-4 md:grid-cols-3">
        <Link className="rounded-md border p-5" to="/review/retrieval">
          <div className="flex items-start justify-between gap-4">
            <FileSearch className="text-muted-foreground size-5" />
            <span className="font-semibold text-2xl">
              {countValue(counts, "retrieval_reviews")}
            </span>
          </div>
          <h2 className="mt-4 text-base font-semibold tracking-normal">
            Retrieval
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            Question, answer, and evidence review
          </p>
        </Link>
        <Link className="rounded-md border p-5" to="/review/fact-decomposition">
          <div className="flex items-start justify-between gap-4">
            <ListChecks className="text-muted-foreground size-5" />
            <span className="font-semibold text-2xl">
              {countValue(counts, "fact_decomp_reviews")}
            </span>
          </div>
          <h2 className="mt-4 text-base font-semibold tracking-normal">
            Fact Decomposition
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            Ordered fact and rubric review
          </p>
        </Link>
        <Link className="rounded-md border p-5" to="/review/relevance">
          <div className="flex items-start justify-between gap-4">
            <SearchCheck className="text-muted-foreground size-5" />
            <span className="font-semibold text-2xl">
              {countValue(counts, "relevance_reviews")}
            </span>
          </div>
          <h2 className="mt-4 text-base font-semibold tracking-normal">
            Relevance
          </h2>
          <p className="text-muted-foreground mt-1 text-sm">
            Retrieved passage grading
          </p>
        </Link>
      </div>
    </div>
  )
}

function countValue(counts: Record<string, number>, key: string): string {
  return String(counts[key] ?? 0)
}
