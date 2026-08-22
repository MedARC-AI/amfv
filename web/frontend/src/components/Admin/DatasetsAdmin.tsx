import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Layers3 } from "lucide-react"
import { useState } from "react"

import { AdminService, type DatasetSummary, type EvalType } from "@/client"
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

export default function DatasetsAdmin() {
  const queryClient = useQueryClient()
  const [name, setName] = useState("")
  const [displayName, setDisplayName] = useState("")
  const [description, setDescription] = useState("")
  const [evalType, setEvalType] = useState<EvalType>("RETRIEVAL")
  const datasetsQuery = useQuery({
    queryKey: ["admin-datasets"],
    queryFn: () => AdminService.readAdminDatasets(),
  })
  const createDataset = useMutation({
    mutationFn: () =>
      AdminService.createAdminDataset({
        requestBody: {
          name,
          display_name: displayName,
          description: description || null,
          eval_type: evalType,
          is_active: true,
        },
      }),
    onSuccess: () => {
      setName("")
      setDisplayName("")
      setDescription("")
      void queryClient.invalidateQueries({ queryKey: ["admin-datasets"] })
    },
  })

  const canSubmit = name.trim().length > 0 && displayName.trim().length > 0

  return (
    <div className="grid gap-6 xl:grid-cols-[0.9fr_1.1fr]">
      <section className="rounded-md border p-5">
        <div className="mb-5 flex items-center gap-3">
          <Layers3 className="text-muted-foreground size-5" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Datasets</h1>
            <p className="text-muted-foreground text-sm">
              Dataset scopes for retrieval and fact decomposition
            </p>
          </div>
        </div>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (canSubmit) {
              createDataset.mutate()
            }
          }}
        >
          <div className="grid gap-2">
            <Label htmlFor="dataset-display-name">Display name</Label>
            <Input
              id="dataset-display-name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
              placeholder="Cardiology Retrieval"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="dataset-name">System name</Label>
            <Input
              id="dataset-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="cardiology-retrieval"
            />
          </div>
          <div className="grid gap-2">
            <Label>Eval type</Label>
            <Select
              value={evalType}
              onValueChange={(value) => setEvalType(value as EvalType)}
            >
              <SelectTrigger data-testid="dataset-eval-type">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="RETRIEVAL">Retrieval</SelectItem>
                <SelectItem value="FACT_DECOMP">Fact decomposition</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="dataset-description">Description</Label>
            <textarea
              id="dataset-description"
              className="border-input min-h-24 w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </div>
          <Button
            type="submit"
            disabled={!canSubmit || createDataset.isPending}
          >
            Create dataset
          </Button>
          {createDataset.isError ? (
            <p className="text-destructive text-sm">
              Could not create dataset.
            </p>
          ) : null}
        </form>
      </section>

      <section className="rounded-md border p-5">
        <h2 className="mb-4 text-base font-semibold tracking-normal">
          Existing datasets
        </h2>
        {datasetsQuery.isLoading ? (
          <p className="text-muted-foreground text-sm">Loading datasets</p>
        ) : null}
        {datasetsQuery.isError ? (
          <p className="text-destructive text-sm">Could not load datasets.</p>
        ) : null}
        <div className="grid gap-3">
          {(datasetsQuery.data ?? []).map((dataset) => (
            <DatasetRow key={dataset.id} dataset={dataset} />
          ))}
        </div>
      </section>
    </div>
  )
}

function DatasetRow({ dataset }: { dataset: DatasetSummary }) {
  return (
    <div className="rounded-md border p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h3 className="font-medium text-sm">{dataset.display_name}</h3>
          <p className="text-muted-foreground text-sm">{dataset.name}</p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="secondary">{dataset.eval_type}</Badge>
          <Badge variant={dataset.is_active ? "default" : "outline"}>
            {dataset.is_active ? "Active" : "Inactive"}
          </Badge>
        </div>
      </div>
    </div>
  )
}
