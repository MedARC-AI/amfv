import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { ExternalLink, FileText } from "lucide-react"
import { useMemo, useState } from "react"

import {
  AdminService,
  type DatasetSummary,
  type DocumentImportSummary,
  type DocumentSummary,
} from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { apiErrorMessage, sourceDocumentUrl } from "@/utils"

export default function DocumentsAdmin({ canImport }: { canImport: boolean }) {
  const queryClient = useQueryClient()
  const [datasetId, setDatasetId] = useState("")
  const [importDatasetId, setImportDatasetId] = useState("")
  const [importFile, setImportFile] = useState<File | null>(null)
  const [dryRun, setDryRun] = useState(true)
  const [importSummary, setImportSummary] =
    useState<DocumentImportSummary | null>(null)
  const [title, setTitle] = useState("")
  const [externalId, setExternalId] = useState("")
  const [content, setContent] = useState("")
  const [selectedDocumentId, setSelectedDocumentId] = useState<number | null>(
    null,
  )
  const datasetsQuery = useQuery({
    queryKey: ["admin-datasets"],
    queryFn: () => AdminService.readAdminDatasets(),
  })
  const documentsQuery = useQuery({
    queryKey: ["admin-documents"],
    queryFn: () => AdminService.readAdminDocuments(),
  })
  const selectedDocumentQuery = useQuery({
    queryKey: ["admin-document", selectedDocumentId],
    queryFn: () =>
      AdminService.readAdminDocument({
        documentId: selectedDocumentId as number,
      }),
    enabled: selectedDocumentId !== null,
  })
  const createDocument = useMutation({
    mutationFn: () =>
      AdminService.createAdminDocument({
        requestBody: {
          dataset_id: Number(datasetId),
          title,
          external_id: externalId || null,
          content,
        },
      }),
    onSuccess: (document) => {
      setTitle("")
      setExternalId("")
      setContent("")
      setSelectedDocumentId(document.id)
      void queryClient.invalidateQueries({ queryKey: ["admin-documents"] })
    },
  })
  const toggleDocument = useMutation({
    mutationFn: (documentId: number) =>
      AdminService.toggleAdminDocument({ documentId }),
    onSuccess: (document) => {
      void queryClient.invalidateQueries({ queryKey: ["admin-documents"] })
      void queryClient.invalidateQueries({
        queryKey: ["admin-document", document.id],
      })
    },
  })
  const importDocuments = useMutation({
    mutationFn: () => {
      if (!importFile) {
        throw new Error("Select a JSONL artifact to import.")
      }
      return AdminService.importAdminDocuments({
        formData: {
          dataset_id: Number(importDatasetId),
          dry_run: dryRun,
          // The generated schema currently describes UploadFile as string, but
          // its generated FormData serializer correctly accepts File/Blob.
          file: importFile as unknown as string,
        },
      })
    },
    onSuccess: (summary) => {
      setImportSummary(summary)
      if (!summary.dry_run) {
        void queryClient.invalidateQueries({ queryKey: ["admin-documents"] })
      }
    },
  })
  const datasetsById = useMemo(() => {
    return new Map(
      (datasetsQuery.data ?? []).map((dataset) => [dataset.id, dataset]),
    )
  }, [datasetsQuery.data])
  const canSubmit =
    datasetId.length > 0 && title.trim().length > 0 && content.trim().length > 0
  const importDatasets = (datasetsQuery.data ?? []).filter(
    (dataset) => dataset.eval_type === "RETRIEVAL" && dataset.is_active,
  )
  const canImportArtifact =
    canImport && importDatasetId.length > 0 && importFile !== null

  return (
    <div className="grid gap-6 xl:grid-cols-[0.85fr_1.15fr]">
      <section className="rounded-md border p-5">
        <div className="mb-5 flex items-center gap-3">
          <FileText className="text-muted-foreground size-5" />
          <div>
            <h1 className="text-2xl font-bold tracking-tight">Documents</h1>
            <p className="text-muted-foreground text-sm">
              Complete source documents
            </p>
          </div>
        </div>
        <form
          className="flex flex-col gap-4"
          onSubmit={(event) => {
            event.preventDefault()
            if (canSubmit) {
              createDocument.mutate()
            }
          }}
        >
          <div className="grid gap-2">
            <Label>Dataset</Label>
            <Select value={datasetId} onValueChange={setDatasetId}>
              <SelectTrigger data-testid="admin-document-dataset">
                <SelectValue placeholder="Select dataset" />
              </SelectTrigger>
              <SelectContent>
                {(datasetsQuery.data ?? []).map((dataset) => (
                  <SelectItem key={dataset.id} value={String(dataset.id)}>
                    {dataset.display_name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="grid gap-2">
            <Label htmlFor="document-title">Title</Label>
            <Input
              id="document-title"
              value={title}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Clinical source note"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="document-external-id">External ID</Label>
            <Input
              id="document-external-id"
              value={externalId}
              onChange={(event) => setExternalId(event.target.value)}
              placeholder="source-001"
            />
          </div>
          <div className="grid gap-2">
            <Label htmlFor="document-content">Content</Label>
            <textarea
              id="document-content"
              className="border-input min-h-40 w-full rounded-md border bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
              value={content}
              onChange={(event) => setContent(event.target.value)}
            />
          </div>
          <Button
            type="submit"
            disabled={!canSubmit || createDocument.isPending}
          >
            Create document
          </Button>
          {createDocument.isError ? (
            <p className="text-destructive text-sm">
              Could not create document.
            </p>
          ) : null}
        </form>

        {canImport ? (
          <form
            className="mt-6 flex flex-col gap-4 border-t pt-6"
            onSubmit={(event) => {
              event.preventDefault()
              if (canImportArtifact) {
                setImportSummary(null)
                importDocuments.mutate()
              }
            }}
          >
            <div>
              <h2 className="text-base font-semibold tracking-normal">
                Import normalized JSONL
              </h2>
              <p className="text-muted-foreground mt-1 text-sm">
                Upload a versioned source-document artifact. Validate with a dry
                run before creating documents.
              </p>
            </div>
            <div className="grid gap-2">
              <Label>Retrieval dataset</Label>
              <Select
                value={importDatasetId}
                onValueChange={setImportDatasetId}
              >
                <SelectTrigger data-testid="admin-document-import-dataset">
                  <SelectValue placeholder="Select active retrieval dataset" />
                </SelectTrigger>
                <SelectContent>
                  {importDatasets.map((dataset) => (
                    <SelectItem key={dataset.id} value={String(dataset.id)}>
                      {dataset.display_name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-2">
              <Label htmlFor="document-import-file">JSONL artifact</Label>
              <Input
                accept=".jsonl,application/x-ndjson"
                id="document-import-file"
                onChange={(event) => {
                  setImportFile(event.target.files?.[0] ?? null)
                  setImportSummary(null)
                }}
                type="file"
              />
            </div>
            <div className="flex items-center gap-2">
              <Checkbox
                checked={dryRun}
                id="document-import-dry-run"
                onCheckedChange={(checked) => setDryRun(checked === true)}
              />
              <Label htmlFor="document-import-dry-run">
                Dry run (no writes)
              </Label>
            </div>
            <Button
              disabled={!canImportArtifact || importDocuments.isPending}
              type="submit"
            >
              {dryRun ? "Validate artifact" : "Import documents"}
            </Button>
            {importDocuments.isError ? (
              <p className="text-destructive text-sm">
                {apiErrorMessage(importDocuments.error)}
              </p>
            ) : null}
            {importSummary ? <ImportSummary summary={importSummary} /> : null}
          </form>
        ) : null}
      </section>

      <section className="flex flex-col gap-4">
        <div className="rounded-md border p-5">
          <h2 className="mb-4 text-base font-semibold tracking-normal">
            Source documents
          </h2>
          {documentsQuery.isLoading ? (
            <p className="text-muted-foreground text-sm">Loading documents</p>
          ) : null}
          {documentsQuery.isError ? (
            <p className="text-destructive text-sm">
              Could not load documents.
            </p>
          ) : null}
          <div className="grid gap-3">
            {(documentsQuery.data ?? []).map((document) => (
              <DocumentRow
                key={document.id}
                document={document}
                dataset={datasetsById.get(document.dataset_id)}
                onPreview={() => setSelectedDocumentId(document.id)}
                onToggle={() => toggleDocument.mutate(document.id)}
              />
            ))}
          </div>
        </div>

        {selectedDocumentQuery.data ? (
          <DocumentDetailPanel document={selectedDocumentQuery.data} />
        ) : null}
      </section>
    </div>
  )
}

function ImportSummary({ summary }: { summary: DocumentImportSummary }) {
  return (
    <output className="block rounded-md border p-3 text-sm">
      <p className="font-medium">
        {summary.dry_run ? "Dry-run result" : "Import result"}
      </p>
      <p className="text-muted-foreground mt-1">
        Created {summary.created} · unchanged {summary.unchanged} · rejected{" "}
        {summary.rejected}
      </p>
      {summary.errors.length > 0 ? (
        <ul className="text-destructive mt-3 max-h-40 list-disc space-y-1 overflow-auto pl-5">
          {summary.errors.map((error) => (
            <li key={`${error.line}-${error.message}`}>
              Line {error.line}: {error.message}
            </li>
          ))}
        </ul>
      ) : null}
    </output>
  )
}

function DocumentRow({
  document,
  dataset,
  onPreview,
  onToggle,
}: {
  document: DocumentSummary
  dataset: DatasetSummary | undefined
  onPreview: () => void
  onToggle: () => void
}) {
  const sourceUrl = sourceDocumentUrl(document)
  return (
    <div
      className="rounded-md border p-4"
      data-testid={`admin-document-${document.external_id}`}
    >
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="font-medium text-sm">{document.title}</h3>
          <p className="text-muted-foreground text-sm">
            {dataset?.display_name ?? `Dataset ${document.dataset_id}`}
          </p>
          <p className="text-muted-foreground text-xs">
            {document.external_id}
          </p>
          {sourceUrl ? (
            <a
              className="mt-1 inline-flex items-center gap-1 text-xs text-primary underline-offset-4 hover:underline"
              href={sourceUrl}
              rel="noreferrer"
              target="_blank"
            >
              Source URL
              <ExternalLink className="size-3" />
            </a>
          ) : null}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant={document.is_active ? "default" : "outline"}>
            {document.is_active ? "Active" : "Inactive"}
          </Badge>
          <Button type="button" variant="outline" size="sm" onClick={onPreview}>
            Preview
          </Button>
          <Button type="button" variant="outline" size="sm" onClick={onToggle}>
            {document.is_active ? "Deactivate" : "Activate"}
          </Button>
        </div>
      </div>
    </div>
  )
}

function DocumentDetailPanel({
  document,
}: {
  document: Awaited<ReturnType<typeof AdminService.readAdminDocument>>
}) {
  const sourceUrl = sourceDocumentUrl(document)
  return (
    <div className="rounded-md border p-5">
      <div className="mb-4 flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-semibold tracking-normal">
            {document.title}
          </h2>
          <p className="text-muted-foreground text-sm">
            Complete document text
          </p>
          {sourceUrl ? (
            <a
              className="mt-1 inline-flex items-center gap-1 text-sm text-primary underline-offset-4 hover:underline"
              href={sourceUrl}
              rel="noreferrer"
              target="_blank"
            >
              View source URL
              <ExternalLink className="size-3.5" />
            </a>
          ) : null}
        </div>
        <Badge variant={document.is_active ? "default" : "outline"}>
          {document.is_active ? "Active" : "Inactive"}
        </Badge>
      </div>
      <p className="text-muted-foreground max-h-32 overflow-auto whitespace-pre-wrap rounded-md border p-3 text-sm">
        {document.content}
      </p>
      <div className="mt-4 grid gap-3">
        {(document.chunks ?? []).map((chunk) => (
          <div key={chunk.id} className="rounded-md border p-3">
            <div className="text-muted-foreground mb-2 text-xs">
              Backing text {chunk.position}
            </div>
            <p className="whitespace-pre-wrap text-sm">{chunk.text}</p>
          </div>
        ))}
      </div>
    </div>
  )
}
