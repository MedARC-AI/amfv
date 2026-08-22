import { useQuery } from "@tanstack/react-query"
import { BarChart3 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"

import {
  type AdminAgreementMetric,
  type AdminInterUserAgreementMetric,
  AdminService,
  type AdminUserMetricPage,
  type ReviewerKind,
} from "@/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { apiErrorMessage } from "@/utils"

const USER_METRICS_PAGE_SIZE = 50
const MAX_AGREEMENT_JUDGMENTS = 2000

export default function MetricsAdmin() {
  const [datasetId, setDatasetId] = useState("all")
  const [reviewerKind, setReviewerKind] = useState<ReviewerKind | "all">("all")
  const [minOverlap, setMinOverlap] = useState(1)
  const [userMetricsOffset, setUserMetricsOffset] = useState(0)
  const [leftUserId, setLeftUserId] = useState("")
  const [rightUserId, setRightUserId] = useState("")
  const datasetIdParam = datasetId === "all" ? null : Number(datasetId)
  const datasetsQuery = useQuery({
    queryKey: ["admin-datasets"],
    queryFn: () => AdminService.readAdminDatasets(),
  })
  const userMetricsQuery = useQuery({
    queryKey: ["admin-user-metrics", datasetId, userMetricsOffset],
    queryFn: () =>
      AdminService.readUserMetrics({
        datasetId: datasetIdParam,
        offset: userMetricsOffset,
        limit: USER_METRICS_PAGE_SIZE,
      }),
  })
  const pagedUsers = useMemo(
    () => userMetricsQuery.data?.items ?? [],
    [userMetricsQuery.data?.items],
  )
  const pagedUserIds = useMemo(
    () => pagedUsers.map((user) => user.user_id),
    [pagedUsers],
  )

  useEffect(() => {
    const nextLeftUserId = pagedUserIds.includes(leftUserId)
      ? leftUserId
      : (pagedUserIds[0] ?? "")
    const nextRightUserId =
      pagedUserIds.includes(rightUserId) && rightUserId !== nextLeftUserId
        ? rightUserId
        : (pagedUserIds.find((userId) => userId !== nextLeftUserId) ?? "")
    setLeftUserId(nextLeftUserId)
    setRightUserId(nextRightUserId)
  }, [leftUserId, pagedUserIds, rightUserId])

  const hasDistinctReviewers =
    leftUserId.length > 0 &&
    rightUserId.length > 0 &&
    leftUserId !== rightUserId
  const agreementQuery = useQuery({
    queryKey: ["admin-agreement", datasetId, reviewerKind],
    queryFn: () =>
      AdminService.readAgreementMetrics({
        datasetId: datasetIdParam,
        maxJudgments: MAX_AGREEMENT_JUDGMENTS,
        reviewerKind: reviewerKind === "all" ? null : reviewerKind,
      }),
  })
  const interUserQuery = useQuery({
    queryKey: [
      "admin-inter-user-agreement",
      datasetId,
      leftUserId,
      rightUserId,
      minOverlap,
    ],
    queryFn: () =>
      AdminService.readInterUserAgreement({
        datasetId: datasetIdParam,
        leftUserId,
        maxJudgments: MAX_AGREEMENT_JUDGMENTS,
        minOverlap,
        rightUserId,
      }),
    enabled: hasDistinctReviewers,
  })

  const selectLeftReviewer = (userId: string) => {
    setLeftUserId(userId)
    if (userId === rightUserId) {
      setRightUserId(
        pagedUsers.find((user) => user.user_id !== userId)?.user_id ?? "",
      )
    }
  }

  const selectRightReviewer = (userId: string) => {
    setRightUserId(userId)
    if (userId === leftUserId) {
      setLeftUserId(
        pagedUsers.find((user) => user.user_id !== userId)?.user_id ?? "",
      )
    }
  }

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
            <Select
              onValueChange={(value) => {
                setDatasetId(value)
                setUserMetricsOffset(0)
              }}
              value={datasetId}
            >
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
          page={userMetricsQuery.data}
          loading={userMetricsQuery.isLoading}
          errorMessage={
            userMetricsQuery.isError
              ? apiErrorMessage(userMetricsQuery.error)
              : null
          }
          onNextPage={() => {
            const nextOffset = userMetricsQuery.data?.next_offset
            if (nextOffset !== null && nextOffset !== undefined) {
              setUserMetricsOffset(nextOffset)
            }
          }}
          onPreviousPage={() => {
            const offset = userMetricsQuery.data?.offset ?? 0
            const limit = userMetricsQuery.data?.limit ?? USER_METRICS_PAGE_SIZE
            setUserMetricsOffset(Math.max(0, offset - limit))
          }}
        />
      </section>

      <div className="grid gap-6 xl:grid-cols-2">
        <section className="rounded-md border p-5">
          <h2 className="mb-4 text-base font-semibold tracking-normal">
            Agreement
          </h2>
          <p className="mb-4 text-muted-foreground text-sm">
            Limited to {MAX_AGREEMENT_JUDGMENTS.toLocaleString()} judgments.
          </p>
          <AgreementTable
            rows={agreementQuery.data ?? []}
            loading={agreementQuery.isLoading}
            errorMessage={
              agreementQuery.isError
                ? apiErrorMessage(agreementQuery.error)
                : null
            }
          />
        </section>

        <section className="rounded-md border p-5">
          <h2 className="mb-4 text-base font-semibold tracking-normal">
            Inter-user agreement
          </h2>
          <div className="mb-4 grid gap-4 sm:grid-cols-2">
            <div className="grid gap-2">
              <Label>Left reviewer</Label>
              <Select
                disabled={pagedUsers.length < 2}
                onValueChange={selectLeftReviewer}
                value={leftUserId || undefined}
              >
                <SelectTrigger
                  aria-label="Left reviewer"
                  data-testid="admin-metrics-left-reviewer"
                >
                  <SelectValue placeholder="Choose reviewer" />
                </SelectTrigger>
                <SelectContent>
                  {pagedUsers.map((user) => (
                    <SelectItem key={user.user_id} value={user.user_id}>
                      {user.email}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label>Right reviewer</Label>
              <Select
                disabled={pagedUsers.length < 2}
                onValueChange={selectRightReviewer}
                value={rightUserId || undefined}
              >
                <SelectTrigger
                  aria-label="Right reviewer"
                  data-testid="admin-metrics-right-reviewer"
                >
                  <SelectValue placeholder="Choose reviewer" />
                </SelectTrigger>
                <SelectContent>
                  {pagedUsers.map((user) => (
                    <SelectItem key={user.user_id} value={user.user_id}>
                      {user.email}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <p className="mb-4 text-muted-foreground text-sm">
            Uses at most {MAX_AGREEMENT_JUDGMENTS.toLocaleString()} judgments
            from the currently visible user page.
          </p>
          <InterUserAgreementTable
            available={hasDistinctReviewers}
            rows={interUserQuery.data ?? []}
            loading={interUserQuery.isLoading}
            errorMessage={
              interUserQuery.isError
                ? apiErrorMessage(interUserQuery.error)
                : null
            }
          />
        </section>
      </div>
    </div>
  )
}

function UserMetricsTable({
  page,
  loading,
  errorMessage,
  onNextPage,
  onPreviousPage,
}: {
  page: AdminUserMetricPage | undefined
  loading: boolean
  errorMessage: string | null
  onNextPage: () => void
  onPreviousPage: () => void
}) {
  if (loading) {
    return <p className="text-muted-foreground text-sm">Loading user metrics</p>
  }
  if (errorMessage) {
    return (
      <p className="text-destructive text-sm" role="alert">
        {errorMessage}
      </p>
    )
  }
  const rows = page?.items ?? []
  const pageOffset = page?.offset ?? 0
  const total = page?.total ?? 0
  if (rows.length === 0) {
    return <p className="text-muted-foreground text-sm">No user metrics yet.</p>
  }
  return (
    <div className="space-y-4">
      <p aria-live="polite" className="text-muted-foreground text-sm">
        Showing {pageOffset + 1}-{pageOffset + rows.length} of {total} users
      </p>
      <div className="overflow-auto">
        <table className="w-full min-w-[680px] text-sm">
          <thead className="text-muted-foreground text-left">
            <tr className="border-b">
              <th className="py-2 pr-4 font-medium">User</th>
              <th className="py-2 pr-4 font-medium">Role</th>
              <th className="py-2 pr-4 font-medium">Kind</th>
              <th className="py-2 pr-4 text-right font-medium">Authored</th>
              <th className="py-2 pr-4 text-right font-medium">Fact reviews</th>
              <th className="py-2 pr-4 text-right font-medium">Retrieval QA</th>
              <th className="py-2 pr-4 text-right font-medium">Relevance</th>
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
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <nav
        aria-label="User metrics pages"
        className="flex flex-wrap items-center justify-between gap-3"
      >
        <Button
          disabled={page?.offset === 0}
          onClick={onPreviousPage}
          type="button"
          variant="outline"
        >
          Previous page
        </Button>
        <Button
          disabled={
            page?.next_offset === null || page?.next_offset === undefined
          }
          onClick={onNextPage}
          type="button"
          variant="outline"
        >
          Next page
        </Button>
      </nav>
    </div>
  )
}

function AgreementTable({
  rows,
  loading,
  errorMessage,
}: {
  rows: AdminAgreementMetric[]
  loading: boolean
  errorMessage: string | null
}) {
  if (loading) {
    return <p className="text-muted-foreground text-sm">Loading agreement</p>
  }
  if (errorMessage) {
    return (
      <p className="text-destructive text-sm" role="alert">
        {errorMessage}
      </p>
    )
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
  available,
  rows,
  loading,
  errorMessage,
}: {
  available: boolean
  rows: AdminInterUserAgreementMetric[]
  loading: boolean
  errorMessage: string | null
}) {
  if (!available) {
    return (
      <p className="text-muted-foreground text-sm">
        Choose two distinct reviewers from this page to compare them.
      </p>
    )
  }
  if (loading) {
    return (
      <p className="text-muted-foreground text-sm">
        Loading inter-user agreement
      </p>
    )
  }
  if (errorMessage) {
    return (
      <p className="text-destructive text-sm" role="alert">
        {errorMessage}
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
