import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { FileCheck2 } from "lucide-react"
import { useMemo, useState } from "react"

import {
  type AdminItemSummary,
  AdminService,
  type DatasetSummary,
  type ItemStatus,
} from "@/client"
import { Badge } from "@/components/ui/badge"
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

export default function ModerationAdmin() {
  const queryClient = useQueryClient()
  const [datasetId, setDatasetId] = useState("all")
  const [status, setStatus] = useState<ItemStatus>("SUBMITTED")
  const [reason, setReason] = useState("")
  const datasetsQuery = useQuery({
    queryKey: ["admin-datasets"],
    queryFn: () => AdminService.readAdminDatasets(),
  })
  const itemsQuery = useQuery({
    queryKey: ["admin-items", datasetId, status],
    queryFn: () =>
      AdminService.readAdminItems({
        datasetId: datasetId === "all" ? null : Number(datasetId),
        status,
      }),
  })
  const approveItem = useMutation({
    mutationFn: (item: AdminItemSummary) =>
      AdminService.approveAdminItem({
        itemId: item.id,
        requestBody: {
          action: "approve",
          dataset_id: item.dataset_id,
          expected_item_revision: item.revision,
          reason: null,
        },
      }),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["admin-items"] })
    },
  })
  const rejectItem = useMutation({
    mutationFn: ({
      item,
      action,
    }: {
      item: AdminItemSummary
      action: "reject" | "return_to_draft"
    }) =>
      AdminService.rejectAdminItem({
        itemId: item.id,
        requestBody: {
          action,
          dataset_id: item.dataset_id,
          expected_item_revision: item.revision,
          reason: reason || null,
        },
      }),
    onSuccess: () => {
      setReason("")
      void queryClient.invalidateQueries({ queryKey: ["admin-items"] })
    },
  })
  const datasetsById = useMemo(() => {
    return new Map(
      (datasetsQuery.data ?? []).map((dataset) => [dataset.id, dataset]),
    )
  }, [datasetsQuery.data])
  const busy = approveItem.isPending || rejectItem.isPending

  return (
    <div className="flex flex-col gap-6">
      <section className="rounded-md border p-5">
        <div className="mb-5 flex items-center gap-3">
          <FileCheck2 className="text-muted-foreground size-5" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Moderation</h1>
            <p className="text-muted-foreground text-sm">
              Review submitted items before they enter active queues
            </p>
          </div>
        </div>
        <div className="grid gap-4 md:grid-cols-[1fr_220px_1fr]">
          <div className="grid gap-2">
            <Label>Dataset</Label>
            <Select value={datasetId} onValueChange={setDatasetId}>
              <SelectTrigger data-testid="admin-moderation-dataset">
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
            <Label>Status</Label>
            <Select
              value={status}
              onValueChange={(value) => setStatus(value as ItemStatus)}
            >
              <SelectTrigger data-testid="admin-moderation-status">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="SUBMITTED">Submitted</SelectItem>
                <SelectItem value="ACTIVE">Active</SelectItem>
                <SelectItem value="DRAFT">Draft</SelectItem>
                <SelectItem value="REJECTED">Rejected</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="moderation-reason">Reason</Label>
            <Input
              id="moderation-reason"
              value={reason}
              onChange={(event) => setReason(event.target.value)}
              placeholder="Optional rejection note"
            />
          </div>
        </div>
      </section>

      <section className="rounded-md border p-5">
        <h2 className="mb-4 text-base font-semibold tracking-normal">Items</h2>
        {itemsQuery.isLoading ? (
          <p className="text-muted-foreground text-sm">Loading items</p>
        ) : null}
        {itemsQuery.isError ? (
          <p className="text-destructive text-sm">Could not load items.</p>
        ) : null}
        <div className="grid gap-3">
          {(itemsQuery.data ?? []).map((item) => (
            <ModerationItemRow
              key={item.id}
              item={item}
              dataset={datasetsById.get(item.dataset_id)}
              busy={busy}
              onApprove={() => approveItem.mutate(item)}
              onReject={() => rejectItem.mutate({ item, action: "reject" })}
              onReturnToDraft={() =>
                rejectItem.mutate({ item, action: "return_to_draft" })
              }
            />
          ))}
        </div>
      </section>
    </div>
  )
}

function ModerationItemRow({
  item,
  dataset,
  busy,
  onApprove,
  onReject,
  onReturnToDraft,
}: {
  item: AdminItemSummary
  dataset: DatasetSummary | undefined
  busy: boolean
  onApprove: () => void
  onReject: () => void
  onReturnToDraft: () => void
}) {
  return (
    <div
      className="rounded-md border p-4"
      data-testid={`admin-item-${item.id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="max-w-3xl">
          <div className="mb-2 flex flex-wrap items-center gap-2">
            <Badge variant="secondary">
              {dataset?.display_name ?? item.dataset_id}
            </Badge>
            <Badge variant="outline">{item.eval_type}</Badge>
            <Badge>{item.status}</Badge>
          </div>
          <h3 className="font-medium text-sm">{item.prompt_text}</h3>
          <p className="text-muted-foreground mt-1 text-xs">
            Item {item.id} · revision {item.revision}
            {item.category ? ` · ${item.category}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy || item.status !== "SUBMITTED"}
            onClick={onApprove}
          >
            Approve
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy || item.status !== "SUBMITTED"}
            onClick={onReject}
          >
            Reject
          </Button>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={busy || item.status !== "SUBMITTED"}
            onClick={onReturnToDraft}
          >
            Return to draft
          </Button>
        </div>
      </div>
    </div>
  )
}
