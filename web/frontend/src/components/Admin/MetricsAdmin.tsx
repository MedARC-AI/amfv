import { useQuery } from "@tanstack/react-query"
import { BarChart3 } from "lucide-react"
import { useState } from "react"

import {
  type AdminAgreementMetric,
  type AdminInterUserAgreementMetric,
  AdminService,
  type AdminUserMetric,
  type ReviewerKind,
} from "@/client"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export default function MetricsAdmin() {
  const [datasetId, setDatasetId] = useState("all")
  const [reviewerKind, setReviewerKind] = useState<ReviewerKind | "all">("all")
  const [minOverlap, setMinOverlap] = useState(1)
  const datasetIdParam = datasetId === "all" ? null : Number(datasetId)
  const datasetsQuery = useQuery({
    queryKey: ["admin-datasets"],
    queryFn: () => AdminService.readAdminDatasets(),
  })
  const userMetricsQuery = useQuery({
    queryKey: ["admin-user-metrics", datasetId],
    queryFn: () =>
      AdminService.readUserMetrics({
        datasetId: datasetIdParam,
      }),
  })
  const agreementQuery = useQuery({
    queryKey: ["admin-agreement", datasetId, reviewerKind],
    queryFn: () =>
      AdminService.readAgreementMetrics({
        datasetId: datasetIdParam,
        reviewerKind: reviewerKind === "all" ? null : reviewerKind,
      }),
  })
  const interUserQuery = useQuery({
    queryKey: ["admin-inter-user-agreement", datasetId, minOverlap],
    queryFn: () =>
      AdminService.readInterUserAgreement({
        datasetId: datasetIdParam,
        minOverlap,
      }),
  })

  return (
    <div className="flex flex-col gap-6">
      <section className="rounded-md border p-5">
        <div className="mb-5 flex items-center gap-3">
          <BarChart3 className="text-muted-foreground size-5" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Metrics</h1>
            <p className="text-muted-foreground text-sm">
              User activity, dataset agreement, and inter-user agreement
            </p>
          </div>
        </div>
        <div className="grid gap-4 md:grid-cols-3">
          <div className="grid gap-2">
            <Label>Dataset</Label>
            <Select value={datasetId} onValueChange={setDatasetId}>
              <SelectTrigger data-testid="admin-metrics-dataset">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All datasets</SelectItem>
                {(datasetsQuery.data ?? []).map((dataset) => (
                  <SelectItem key={dataset.id} value={String(dataset.id)}>
                    {dataset.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label>Agreement reviewer kind</Label>
            <Select
              value={reviewerKind}
              onValueChange={(value) =>
                setReviewerKind(value as ReviewerKind | "all")
              }
            >
              <SelectTrigger data-testid="admin-metrics-reviewer-kind">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All reviewers</SelectItem>
                <SelectItem value="human">Human</SelectItem>
                <SelectItem value="expert">Expert</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="metrics-min-overlap">Minimum overlap</Label>
            <Input
              id="metrics-min-overlap"
              type="number"
              min={1}
              value={minOverlap}
              onChange={(event) =>
                setMinOverlap(Math.max(1, Number(event.target.value) || 1))
              }
            />
          </div>
        </div>
      </section>

      <section className="rounded-md border p-5">
        <h2 className="mb-4 text-base font-semibold tracking-normal">
          User metrics
        </h2>
        <UserMetricsTable
          rows={userMetricsQuery.data ?? []}
          loading={userMetricsQuery.isLoading}
          error={userMetricsQuery.isError}
        />
      </section>

      <div className="grid gap-6 xl:grid-cols-2">
        <section className="rounded-md border p-5">
          <h2 className="mb-4 text-base font-semibold tracking-normal">
            Agreement
          </h2>
          <AgreementTable
            rows={agreementQuery.data ?? []}
            loading={agreementQuery.isLoading}
            error={agreementQuery.isError}
          />
        </section>

        <section className="rounded-md border p-5">
          <h2 className="mb-4 text-base font-semibold tracking-normal">
            Inter-user agreement
          </h2>
          <InterUserAgreementTable
            rows={interUserQuery.data ?? []}
            loading={interUserQuery.isLoading}
            error={interUserQuery.isError}
          />
        </section>
      </div>
    </div>
  )
}

function UserMetricsTable({
  rows,
  loading,
  error,
}: {
  rows: AdminUserMetric[]
  loading: boolean
  error: boolean
}) {
  if (loading) {
    return <p className="text-muted-foreground text-sm">Loading user metrics</p>
  }
  if (error) {
    return (
      <p className="text-destructive text-sm">Could not load user metrics.</p>
    )
  }
  if (rows.length === 0) {
    return <p className="text-muted-foreground text-sm">No user metrics yet.</p>
  }
  return (
    <div className="overflow-auto">
      <table className="w-full min-w-[760px] text-sm">
        <thead className="text-muted-foreground text-left">
          <tr className="border-b">
            <th className="py-2 pr-4 font-medium">User</th>
            <th className="py-2 pr-4 font-medium">Role</th>
            <th className="py-2 pr-4 font-medium">Kind</th>
            <th className="py-2 pr-4 text-right font-medium">Authored</th>
            <th className="py-2 pr-4 text-right font-medium">Fact reviews</th>
            <th className="py-2 pr-4 text-right font-medium">Retrieval QA</th>
            <th className="py-2 pr-4 text-right font-medium">Relevance</th>
            <th className="py-2 text-right font-medium">Mean kappa</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.user_id} className="border-b last:border-0">
              <td className="py-2 pr-4">{row.email}</td>
              <td className="py-2 pr-4">{row.role}</td>
              <td className="py-2 pr-4">{row.reviewer_kind}</td>
              <td className="py-2 pr-4 text-right">{row.authored_items}</td>
              <td className="py-2 pr-4 text-right">
                {row.fact_decomp_reviews}
              </td>
              <td className="py-2 pr-4 text-right">
                {row.retrieval_qa_reviews}
              </td>
              <td className="py-2 pr-4 text-right">
                {row.relevance_judgments}
              </td>
              <td className="py-2 text-right">
                {formatMetric(row.mean_kappa)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AgreementTable({
  rows,
  loading,
  error,
}: {
  rows: AdminAgreementMetric[]
  loading: boolean
  error: boolean
}) {
  if (loading) {
    return <p className="text-muted-foreground text-sm">Loading agreement</p>
  }
  if (error) {
    return <p className="text-destructive text-sm">Could not load agreement.</p>
  }
  if (rows.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">No agreement rows yet.</p>
    )
  }
  return (
    <div className="overflow-auto">
      <table className="w-full text-sm">
        <thead className="text-muted-foreground text-left">
          <tr className="border-b">
            <th className="py-2 pr-4 font-medium">Dimension</th>
            <th className="py-2 pr-4 text-right font-medium">Alpha</th>
            <th className="py-2 text-right font-medium">N</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr
              key={`${row.dimension}-${row.n}-${index}`}
              className="border-b last:border-0"
            >
              <td className="py-2 pr-4">{row.dimension}</td>
              <td className="py-2 pr-4 text-right">
                {formatMetric(row.alpha)}
              </td>
              <td className="py-2 text-right">{row.n}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function InterUserAgreementTable({
  rows,
  loading,
  error,
}: {
  rows: AdminInterUserAgreementMetric[]
  loading: boolean
  error: boolean
}) {
  if (loading) {
    return (
      <p className="text-muted-foreground text-sm">
        Loading inter-user agreement
      </p>
    )
  }
  if (error) {
    return (
      <p className="text-destructive text-sm">
        Could not load inter-user agreement.
      </p>
    )
  }
  if (rows.length === 0) {
    return (
      <p className="text-muted-foreground text-sm">
        No inter-user agreement rows yet.
      </p>
    )
  }
  return (
    <div className="overflow-auto">
      <table className="w-full min-w-[620px] text-sm">
        <thead className="text-muted-foreground text-left">
          <tr className="border-b">
            <th className="py-2 pr-4 font-medium">Dimension</th>
            <th className="py-2 pr-4 font-medium">Left user</th>
            <th className="py-2 pr-4 font-medium">Right user</th>
            <th className="py-2 pr-4 text-right font-medium">Kappa</th>
            <th className="py-2 text-right font-medium">Overlap</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={`${row.dimension}-${row.left_user_id}-${row.right_user_id}`}
              className="border-b last:border-0"
            >
              <td className="py-2 pr-4">{row.dimension}</td>
              <td className="py-2 pr-4 font-mono text-xs">
                {row.left_user_id}
              </td>
              <td className="py-2 pr-4 font-mono text-xs">
                {row.right_user_id}
              </td>
              <td className="py-2 pr-4 text-right">
                {formatMetric(row.kappa)}
              </td>
              <td className="py-2 text-right">{row.overlap}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function formatMetric(value: number | null | undefined) {
  if (value === null || value === undefined) {
    return "n/a"
  }
  return value.toFixed(3)
}
