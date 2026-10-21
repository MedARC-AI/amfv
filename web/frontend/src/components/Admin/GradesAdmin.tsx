import { useMutation, useQuery } from "@tanstack/react-query"
import { ListChecks } from "lucide-react"
import { useEffect, useMemo, useState } from "react"

import { AdminService } from "@/client"
import {
  type ExportBundle,
  type ExportProgress,
  fetchExportBundle,
} from "@/components/Admin/grades/exportAll"
import GradesAnalysis from "@/components/Admin/grades/GradesAnalysis"
import GradesOverview from "@/components/Admin/grades/GradesOverview"
import GradesTaskView from "@/components/Admin/grades/GradesTaskView"
import { buildGradesModel } from "@/components/Admin/grades/gradesModel"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { apiErrorMessage } from "@/utils"

type View = "overview" | "task" | "analysis"

export default function GradesAdmin() {
  const [datasetId, setDatasetId] = useState<string>("")
  const [bundle, setBundle] = useState<ExportBundle | null>(null)
  const [progress, setProgress] = useState<ExportProgress | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [view, setView] = useState<View>("overview")
  const [caseId, setCaseId] = useState<string | null>(null)

  const datasetsQuery = useQuery({
    queryKey: ["admin-datasets"],
    queryFn: () => AdminService.readAdminDatasets(),
  })
  const factDecompDatasets = useMemo(
    () =>
      (datasetsQuery.data ?? []).filter(
        (dataset) => dataset.eval_type === "FACT_DECOMP",
      ),
    [datasetsQuery.data],
  )
  useEffect(() => {
    if (datasetId === "" && factDecompDatasets.length > 0) {
      setDatasetId(String(factDecompDatasets[0].id))
    }
  }, [datasetId, factDecompDatasets])

  const loadGrades = useMutation({
    mutationFn: (selected: number) =>
      fetchExportBundle(selected, (next) => setProgress(next)),
    onSuccess: (data) => {
      setBundle(data)
      setErrorMessage(null)
      setProgress(null)
    },
    onError: (error) => {
      setErrorMessage(apiErrorMessage(error))
      setProgress(null)
    },
  })

  const model = useMemo(
    () =>
      bundle
        ? buildGradesModel(bundle.items, bundle.users, bundle.user_names)
        : null,
    [bundle],
  )

  const openTask = (nextCaseId: string) => {
    setCaseId(nextCaseId)
    setView("task")
  }
  const selectedDataset = factDecompDatasets.find(
    (dataset) => String(dataset.id) === datasetId,
  )

  return (
    <div className="flex flex-col gap-6">
      <section className="rounded-md border p-5">
        <div className="mb-4 flex items-center gap-3">
          <ListChecks className="text-muted-foreground size-5" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Grades</h1>
            <p className="text-muted-foreground text-sm">
              Every reviewer's decisions on the model's claims, and where they
              disagree
            </p>
          </div>
        </div>
        <form
          className="flex flex-wrap items-end gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (datasetId !== "") loadGrades.mutate(Number(datasetId))
          }}
        >
          <div className="grid min-w-72 gap-2">
            <Label>Fact-decomposition dataset</Label>
            <Select onValueChange={setDatasetId} value={datasetId}>
              <SelectTrigger data-testid="admin-grades-dataset">
                <SelectValue placeholder="Choose a dataset" />
              </SelectTrigger>
              <SelectContent>
                {factDecompDatasets.map((dataset) => (
                  <SelectItem key={dataset.id} value={String(dataset.id)}>
                    {dataset.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <Button
            disabled={loadGrades.isPending || datasetId === ""}
            type="submit"
          >
            {loadGrades.isPending ? "Loading grades" : "Load grades"}
          </Button>
          {progress ? (
            <p aria-live="polite" className="text-muted-foreground text-sm">
              Loaded {progress.loaded}
              {progress.total !== null ? ` of ${progress.total}` : ""} records
            </p>
          ) : null}
          {errorMessage ? (
            <p className="text-destructive text-sm" role="alert">
              {errorMessage}
            </p>
          ) : null}
        </form>
        {bundle && selectedDataset ? (
          <p className="text-muted-foreground mt-3 text-xs">
            {selectedDataset.display_name} · {bundle.items.length} records
            loaded at {bundle.exported_at.replace("T", " ").slice(0, 16)}
          </p>
        ) : null}
      </section>

      {model ? (
        <Tabs onValueChange={(value) => setView(value as View)} value={view}>
          <TabsList>
            <TabsTrigger value="overview">Overview</TabsTrigger>
            <TabsTrigger value="task">Task view</TabsTrigger>
            <TabsTrigger value="analysis">Analysis</TabsTrigger>
          </TabsList>
          <TabsContent className="mt-4" value="overview">
            <GradesOverview model={model} onOpenTask={openTask} />
          </TabsContent>
          <TabsContent className="mt-4" value="task">
            <GradesTaskView
              caseId={caseId}
              model={model}
              onChangeTask={setCaseId}
            />
          </TabsContent>
          <TabsContent className="mt-4" value="analysis">
            <GradesAnalysis model={model} onOpenTask={openTask} />
          </TabsContent>
        </Tabs>
      ) : (
        <p className="text-muted-foreground text-sm">
          Load a dataset to see its grades.
        </p>
      )}
    </div>
  )
}
