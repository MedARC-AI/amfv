import { createFileRoute } from "@tanstack/react-router"

import { AuthLayout } from "@/components/Common/AuthLayout"

export const Route = createFileRoute("/recover-password")({
  component: RecoverPassword,
  head: () => ({
    meta: [
      {
        title: "Recover Password - AMFV Web",
      },
    ],
  }),
})

function RecoverPassword() {
  return (
    <AuthLayout>
      <div className="flex flex-col gap-4 text-center">
        <h1 className="text-2xl font-bold">Password recovery unavailable</h1>
        <p className="text-muted-foreground text-sm">
          Password recovery will be enabled after SMTP is configured.
        </p>
      </div>
    </AuthLayout>
  )
}
