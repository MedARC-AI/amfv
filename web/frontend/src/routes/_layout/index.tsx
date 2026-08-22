import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { ClipboardCheck, Loader2, PenLine } from "lucide-react"

import type { RecommendedTask } from "@/client"
import { Button } from "@/components/ui/button"
import { homeSummaryQueryOptions } from "@/lib/queries"

export const Route = createFileRoute("/_layout/")({
  component: Home,
  head: () => ({
    meta: [
      {
        title: "Home - AMFV Web",
      },
    ],
  }),
})

function Home() {
  const summaryQuery = useQuery({
    ...homeSummaryQueryOptions,
  })
  const summary = summaryQuery.data
  const recommendation = summary?.recommended_task
  const counts = summary?.outstanding_counts ?? {}

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-2">
        <p className="text-muted-foreground text-sm">
          {summary?.user.full_name || summary?.user.email || "Loading account"}
        </p>
      </div>

      <section className="grid gap-4 lg:grid-cols-[1.2fr_0.8fr]">
        <div className="rounded-md border p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-base font-semibold tracking-normal">
                Recommended Review
              </h2>
              <p className="text-muted-foreground mt-1 text-sm">
                {summaryQuery.isLoading
                  ? "Loading queue"
                  : recommendation?.reason || "No review tasks are available"}
              </p>
            </div>
            <ClipboardCheck className="text-muted-foreground size-5" />
          </div>
          {summaryQuery.isError ? (
            <p className="mt-4 text-destructive text-sm">
              Could not load the review queue.
            </p>
          ) : (
            <div className="mt-4 min-h-12">
              {summaryQuery.isLoading ? (
                <Loader2 className="text-muted-foreground size-5 animate-spin" />
              ) : (
                <>
                  <h3 className="font-medium text-sm">
                    {recommendation?.title || "Review queue clear"}
                  </h3>
                  {recommendation ? (
                    <p className="text-muted-foreground mt-1 text-sm">
                      {recommendation.eval_type.replace("_", " ")}
                    </p>
                  ) : null}
                </>
              )}
            </div>
          )}
          <div className="mt-6">
            <Button asChild>
              <Link to={reviewRoute(recommendation)}>Review</Link>
            </Button>
          </div>
        </div>

        <div className="rounded-md border p-5">
          <div className="flex items-start justify-between gap-4">
            <div>
              <h2 className="text-base font-semibold tracking-normal">
                Create
              </h2>
              <p className="text-muted-foreground mt-1 text-sm">
                Retrieval and fact decomposition
              </p>
            </div>
            <PenLine className="text-muted-foreground size-5" />
          </div>
          <div className="mt-6">
            <Button asChild variant="outline">
              <Link to="/create">Create</Link>
            </Button>
          </div>
        </div>
      </section>

      <section>
        <h2 className="mb-3 text-base font-semibold tracking-normal">
          Outstanding
        </h2>
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {[
            ["Retrieval Reviews", countValue(counts, "retrieval_reviews")],
            ["Fact Reviews", countValue(counts, "fact_decomp_reviews")],
            ["Relevance Reviews", countValue(counts, "relevance_reviews")],
            ["Drafts", countValue(counts, "draft_items")],
          ].map(([label, value]) => (
            <div key={label} className="rounded-md border p-4">
              <div className="text-2xl font-semibold">{value}</div>
              <div className="text-muted-foreground mt-1 text-sm">{label}</div>
            </div>
          ))}
        </div>
      </section>
    </div>
  )
}

type ReviewRoute =
  | "/review"
  | "/review/retrieval"
  | "/review/fact-decomposition"
  | "/review/relevance"

function reviewRoute(task: RecommendedTask | null | undefined): ReviewRoute {
  if (!task) {
    return "/review"
  }
  if (task.kind === "fact_decomp") {
    return "/review/fact-decomposition"
  }
  if (task.kind === "relevance") {
    return "/review/relevance"
  }
  return "/review/retrieval"
}

function countValue(counts: Record<string, number>, key: string): string {
  return String(counts[key] ?? 0)
}
