import { useQuery } from "@tanstack/react-query"
import { createFileRoute, redirect } from "@tanstack/react-router"
import { Loader2 } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { AuthService } from "@/client"
import { AuthLayout } from "@/components/Common/AuthLayout"
import { UserAccountFields } from "@/components/UserAccount/UserAccountFields"
import { Form } from "@/components/ui/form"
import { LoadingButton } from "@/components/ui/loading-button"
import useAuth from "@/hooks/useAuth"
import { redirectIfAuthenticated } from "@/lib/routeGuards"
import {
  buildPasswordPayload,
  buildUserProfilePayload,
  createUserAccountFormResolver,
  type UserAccountFormData,
} from "@/lib/userProfile"

const searchSchema = z.object({
  token: z.string().catch(""),
})

const inviteRoleLabels = {
  user: "User",
  data_admin: "Data admin",
  admin: "Admin",
} as const

type FormData = UserAccountFormData & {
  password: string
  confirm_password: string
}

export const Route = createFileRoute("/signup")({
  component: SignUp,
  validateSearch: searchSchema,
  beforeLoad: async ({ context, search }) => {
    await redirectIfAuthenticated(context.queryClient)
    if (!search.token) {
      throw redirect({ to: "/login" })
    }
  },
  head: () => ({
    meta: [
      {
        title: "Sign Up - AMFV Web",
      },
    ],
  }),
})

function SignUp() {
  const { token } = Route.useSearch()
  const { signUpMutation } = useAuth()
  const inviteQuery = useQuery({
    queryKey: ["invite-preview", token],
    queryFn: () => AuthService.previewInvite({ token }),
  })
  const form = useForm<FormData, unknown, FormData>({
    resolver: createUserAccountFormResolver<FormData>({
      passwordMode: "required",
      requireProfession: true,
    }),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: {
      email: "",
      full_name: "",
      discord_handle: "",
      medical_profession: "",
      medical_profession_other: "",
      password: "",
      confirm_password: "",
    },
  })

  const onSubmit = (data: FormData) => {
    if (signUpMutation.isPending) return
    signUpMutation.mutate({
      invite_token: token,
      ...buildUserProfilePayload(data),
      ...buildPasswordPayload(data),
      password: data.password,
    })
  }

  return (
    <AuthLayout>
      {inviteQuery.isLoading ? (
        <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" />
          Loading invite
        </div>
      ) : inviteQuery.isError ? (
        <div className="flex flex-col gap-4 text-center">
          <h1 className="text-2xl font-bold">Invite unavailable</h1>
          <p className="text-muted-foreground text-sm">
            This invite link is invalid, expired, or already used.
          </p>
        </div>
      ) : (
        <Form {...form}>
          <form
            className="flex flex-col gap-6"
            onSubmit={form.handleSubmit(onSubmit)}
          >
            <div className="flex flex-col items-center gap-2 text-center">
              <h1 className="text-2xl font-bold">Create your account</h1>
              <p className="text-muted-foreground text-sm">
                Invite for{" "}
                {inviteRoleLabels[
                  inviteQuery.data?.role as keyof typeof inviteRoleLabels
                ] ?? "User"}{" "}
                access.
              </p>
            </div>

            <div className="grid gap-4">
              <UserAccountFields
                form={form}
                passwordMode="required"
                requireProfession
                messageClassName="text-xs"
              />

              <LoadingButton type="submit" loading={signUpMutation.isPending}>
                Create account
              </LoadingButton>
            </div>
          </form>
        </Form>
      )}
    </AuthLayout>
  )
}

export default SignUp
