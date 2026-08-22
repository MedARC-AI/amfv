import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { DatabaseZap, RotateCw } from "lucide-react"
import { useState } from "react"

import {
  AdminService,
  type NiceImportJobStatusResponse,
  type NiceImportLimit,
} from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import useCustomToast from "@/hooks/useCustomToast"

const RUNNING_STATUSES = new Set(["pending", "running"])
const LIMITS: Array<{ value: NiceImportLimit; label: string }> = [
  { value: "10", label: "10" },
  { value: "20", label: "20" },
  { value: "50", label: "50" },
  { value: "all", label: "All" },
]

export default function NiceImportAdmin() {
  const queryClient = useQueryClient()
  const { showErrorToast, showSuccessToast } = useCustomToast()
  const [limit, setLimit] = useState<NiceImportLimit>("10")

  const currentQuery = useQuery({
    queryKey: ["admin-nice-import-current"],
    queryFn: () => AdminService.readCurrentNiceImport(),
    refetchInterval: (query) =>
      query.state.data && RUNNING_STATUSES.has(query.state.data.status)
        ? 3000
        : false,
  })
  const currentJob = currentQuery.data ?? null
  const isActive = currentJob ? RUNNING_STATUSES.has(currentJob.status) : false

  const startImport = useMutation({
    mutationFn: () =>
      AdminService.startNiceImport({
        requestBody: { limit: requestLimit(limit) },
      }),
    onSuccess: (job) => {
      showSuccessToast(
        `Started NICE import (${formatLimit(job.requested_limit)})`,
      )
      invalidateNiceQueries(queryClient)
    },
    onError: (error: Error) => showErrorToast(error.message),
  })

  const cancelImport = useMutation({
    mutationFn: (jobId: number) => AdminService.cancelNiceImport({ jobId }),
    onSuccess: () => {
      showSuccessToast("Cancelled NICE import")
      invalidateNiceQueries(queryClient)
    },
    onError: (error: Error) => showErrorToast(error.message),
  })

  return (
    <section className="rounded-md border p-5">
      <div className="mb-5 flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-center gap-3">
          <DatabaseZap className="text-muted-foreground size-5" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">NICE Import</h1>
            <p className="text-muted-foreground text-sm">
              Local NICE recommendation cache
            </p>
          </div>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => currentQuery.refetch()}
          disabled={currentQuery.isFetching}
        >
          <RotateCw
            className={
              currentQuery.isFetching ? "size-4 animate-spin" : "size-4"
            }
          />
          Refresh
        </Button>
      </div>

      <div className="grid gap-5 lg:grid-cols-[minmax(220px,0.45fr)_1fr]">
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            startImport.mutate()
          }}
        >
          <div className="grid gap-2">
            <Label>Count</Label>
            <Select
              value={limit}
              onValueChange={(value) => setLimit(value as NiceImportLimit)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {LIMITS.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button type="submit" disabled={isActive || startImport.isPending}>
            {startImport.isPending ? "Starting..." : "Start import"}
          </Button>
          {isActive && currentJob ? (
            <Button
              type="button"
              variant="outline"
              disabled={cancelImport.isPending}
              onClick={() => cancelImport.mutate(currentJob.id)}
            >
              {cancelImport.isPending ? "Cancelling..." : "Cancel import"}
            </Button>
          ) : null}
        </form>

        <NiceImportStatus job={currentJob} isLoading={currentQuery.isLoading} />
      </div>
    </section>
  )
}

function NiceImportStatus({
  job,
  isLoading,
}: {
  job: NiceImportJobStatusResponse | null
  isLoading: boolean
}) {
  if (isLoading) {
    return <p className="text-muted-foreground text-sm">Loading status</p>
  }
  if (!job) {
    return <p className="text-muted-foreground text-sm">No NICE import yet</p>
  }

  const target = job.target_count ?? "All"
  const attempted = job.completed_count + job.failed_count

  return (
    <div className="rounded-md border p-4">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <div>
          <h2 className="text-base font-semibold tracking-normal">
            Job #{job.id}
          </h2>
          <p className="text-muted-foreground text-sm">
            Requested {formatLimit(job.requested_limit)}
          </p>
        </div>
        <Badge variant={job.status === "failed" ? "destructive" : "secondary"}>
          {job.status}
        </Badge>
      </div>
      <dl className="grid gap-3 sm:grid-cols-2">
        <Metric label="Completed" value={String(job.completed_count)} />
        <Metric label="Failed" value={String(job.failed_count)} />
        <Metric label="Attempted" value={`${attempted} / ${target}`} />
        <Metric label="Heartbeat" value={formatDate(job.heartbeat_at)} />
        <Metric label="Started" value={formatDate(job.started_at)} />
        <Metric label="Finished" value={formatDate(job.finished_at)} />
      </dl>
      {job.last_error ? (
        <p className="text-destructive mt-4 text-sm">{job.last_error}</p>
      ) : null}
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-muted-foreground text-xs">{label}</dt>
      <dd className="text-sm font-medium">{value}</dd>
    </div>
  )
}

function formatLimit(limit: NiceImportLimit) {
  return limit === "all" ? "All" : limit
}

function requestLimit(limit: NiceImportLimit): 10 | 20 | 50 | "all" {
  return limit === "all" ? "all" : (Number(limit) as 10 | 20 | 50)
}

function formatDate(value: string | null | undefined) {
  if (!value) return "-"
  return new Date(value).toLocaleString()
}

function invalidateNiceQueries(queryClient: ReturnType<typeof useQueryClient>) {
  void queryClient.invalidateQueries({
    queryKey: ["admin-nice-import-current"],
  })
  void queryClient.invalidateQueries({ queryKey: ["admin-documents"] })
  void queryClient.invalidateQueries({ queryKey: ["nice-downloads"] })
}
