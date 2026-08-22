import { AlertCircle, CheckCircle2 } from "lucide-react"

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"

type ValidationFlag = {
  level?: string
  message?: string
  [key: string]: unknown
}

type ValidationMessagesProps = {
  ok?: boolean
  flags?: ValidationFlag[]
}

export function ValidationMessages({
  ok,
  flags = [],
}: ValidationMessagesProps) {
  if (flags.length === 0) {
    if (ok !== true) {
      return null
    }
    return (
      <Alert>
        <CheckCircle2 />
        <AlertTitle>Validation passed</AlertTitle>
        <AlertDescription>No issues found.</AlertDescription>
      </Alert>
    )
  }

  return (
    <div className="space-y-2">
      {flags.map((flag, index) => (
        <Alert
          key={`${flag.level ?? "flag"}-${index}`}
          variant={flag.level === "error" ? "destructive" : "default"}
        >
          <AlertCircle />
          <AlertTitle>{flag.level ?? "warning"}</AlertTitle>
          <AlertDescription>
            {flag.message ?? "Validation issue"}
          </AlertDescription>
        </Alert>
      ))}
    </div>
  )
}
