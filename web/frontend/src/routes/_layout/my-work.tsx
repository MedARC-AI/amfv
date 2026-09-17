import { useQuery } from "@tanstack/react-query"
import { createFileRoute, Link } from "@tanstack/react-router"
import { Loader2 } from "lucide-react"
import { useState } from "react"
import { CreateService } from "@/client"
import { Button } from "@/components/ui/button"
import useAuth from "@/hooks/useAuth"

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
  const { user } = useAuth()
  return user ? (
    <OwnedWork key={user.id} userId={user.id} />
  ) : (
    <p>Loading your account…</p>
  )
}

function OwnedWork({ userId }: { userId: string }) {
  const [offset, setOffset] = useState(0)
  const drafts = useQuery({
    queryKey: ["fact-drafts", userId, offset],
    queryFn: () =>
      CreateService.listOwnedFactDecompItems({
        status: "DRAFT",
        offset,
        limit: 20,
      }),
  })
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
      <section className="space-y-3" aria-label="Fact decomposition drafts">
        <h2 className="text-lg font-semibold">Fact decomposition drafts</h2>
        {drafts.isPending ? (
          <p>Loading drafts…</p>
        ) : drafts.isError ? (
          <div>
            <p>Could not load your drafts.</p>
            <Button onClick={() => void drafts.refetch()}>Retry drafts</Button>
          </div>
        ) : (
          <>
            {drafts.data.items.length === 0 ? (
              <p>No saved fact drafts on this page.</p>
            ) : (
              <ul className="space-y-2">
                {drafts.data.items.map((item) => (
                  <li key={item.id} className="rounded-md border p-3">
                    <Link
                      to="/create/fact-decomposition"
                      search={{ item_id: item.id }}
                      className="font-medium underline"
                    >
                      Resume draft {item.id}
                    </Link>
                    <p>{item.source_preview || "Empty source"}</p>
                    <p className="text-sm text-muted-foreground">
                      {item.dataset_name} · Revision {item.item_revision} ·{" "}
                      {new Date(item.updated_at).toLocaleString()}
                    </p>
                  </li>
                ))}
              </ul>
            )}
            <div className="flex gap-2">
              <Button
                variant="outline"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - 20))}
              >
                Previous drafts
              </Button>
              <Button
                variant="outline"
                disabled={offset + 20 >= drafts.data.total}
                onClick={() => setOffset(offset + 20)}
              >
                Next drafts
              </Button>
            </div>
          </>
        )}
      </section>
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
