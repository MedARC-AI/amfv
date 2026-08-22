import { useMutation, useQuery } from "@tanstack/react-query"
import { ListChecks } from "lucide-react"
import { useState } from "react"

import { AdminService, type AdminTaskGenerationResult } from "@/client"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"

export default function TaskGenerationAdmin() {
  const [datasetId, setDatasetId] = useState("")
  const [result, setResult] = useState<AdminTaskGenerationResult | null>(null)
  const datasetsQuery = useQuery({
    queryKey: ["admin-datasets"],
    queryFn: () => AdminService.readAdminDatasets(),
  })
  const eligibleDatasets = (datasetsQuery.data ?? []).filter(
    (dataset) => dataset.eval_type === "FACT_DECOMP",
  )
  const generateTasks = useMutation({
    mutationFn: () =>
      AdminService.generateDatasetTasks({ datasetId: Number(datasetId) }),
    onSuccess: setResult,
  })

  return (
    <div className="grid gap-6 xl:grid-cols-[0.85fr_1.15fr]">
      <section className="rounded-md border p-5">
        <div className="mb-5 flex items-center gap-3">
          <ListChecks className="text-muted-foreground size-5" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Tasks</h1>
            <p className="text-muted-foreground text-sm">
              Generate fact-decomposition review tasks from active items
            </p>
          </div>
        </div>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (datasetId) {
              generateTasks.mutate()
            }
          }}
        >
          <div className="grid gap-2">
            <Label>Dataset</Label>
            <Select value={datasetId} onValueChange={setDatasetId}>
              <SelectTrigger data-testid="admin-task-dataset">
                <SelectValue placeholder="Select fact dataset" />
              </SelectTrigger>
              <SelectContent>
                {eligibleDatasets.map((dataset) => (
                  <SelectItem key={dataset.id} value={String(dataset.id)}>
                    {dataset.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button
            type="submit"
            disabled={!datasetId || generateTasks.isPending}
          >
            Generate tasks
          </Button>
          {generateTasks.isError ? (
            <p className="text-destructive text-sm">
              Could not generate tasks.
            </p>
          ) : null}
        </form>
      </section>

      <section className="rounded-md border p-5">
        <h2 className="mb-4 text-base font-semibold tracking-normal">
          Generation result
        </h2>
        {result ? (
          <div className="grid gap-3 sm:grid-cols-3">
            <StatTile label="Dataset" value={result.dataset_id} />
            <StatTile label="Created" value={result.created} />
            <StatTile label="Existing" value={result.existing} />
          </div>
        ) : (
          <p className="text-muted-foreground text-sm">
            Select a dataset to generate missing review tasks.
          </p>
        )}
      </section>
    </div>
  )
}

function StatTile({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="rounded-md border p-4">
      <div className="text-muted-foreground text-xs">{label}</div>
      <div className="mt-2 text-2xl font-semibold">{value}</div>
    </div>
  )
}
