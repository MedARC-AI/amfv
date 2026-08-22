import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Pencil } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"

import { type UserPublic, UsersService, type UserUpdate } from "@/client"
import { UserAccountFields } from "@/components/UserAccount/UserAccountFields"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { DropdownMenuItem } from "@/components/ui/dropdown-menu"
import { Form } from "@/components/ui/form"
import { LoadingButton } from "@/components/ui/loading-button"
import useCustomToast from "@/hooks/useCustomToast"
import {
  buildAdminUserPayload,
  buildPasswordPayload,
  createUserAccountFormResolver,
  resolveProfessionFormValues,
  type UserAccountFormData,
} from "@/lib/userProfile"
import { handleError } from "@/utils"

type FormData = UserAccountFormData & {
  is_superuser: boolean
  is_active: boolean
}

interface EditUserProps {
  user: UserPublic
  onSuccess: () => void
}

const EditUser = ({ user, onSuccess }: EditUserProps) => {
  const [isOpen, setIsOpen] = useState(false)
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const professionValues = resolveProfessionFormValues(user.medical_profession)

  const form = useForm<FormData, unknown, FormData>({
    resolver: createUserAccountFormResolver<FormData>({
      passwordMode: "optional",
      adminFields: true,
    }),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: {
      email: user.email,
      full_name: user.full_name ?? "",
      discord_handle: user.discord_handle ?? "",
      ...professionValues,
      password: "",
      confirm_password: "",
      is_superuser: user.is_superuser,
      is_active: user.is_active,
    },
  })

  const mutation = useMutation({
    mutationFn: (data: UserUpdate) =>
      UsersService.updateUser({ userId: user.id, requestBody: data }),
    onSuccess: () => {
      showSuccessToast("User updated successfully")
      setIsOpen(false)
      onSuccess()
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: ["users"] })
    },
  })

  const onSubmit = (data: FormData) => {
    const requestBody: UserUpdate = {
      ...buildAdminUserPayload(data),
      ...buildPasswordPayload(data),
    }
    mutation.mutate(requestBody)
  }

  return (
    <Dialog open={isOpen} onOpenChange={setIsOpen}>
      <DropdownMenuItem
        onSelect={(e) => e.preventDefault()}
        onClick={() => setIsOpen(true)}
      >
        <Pencil />
        Edit User
      </DropdownMenuItem>
      <DialogContent className="sm:max-w-md">
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)}>
            <DialogHeader>
              <DialogTitle>Edit User</DialogTitle>
              <DialogDescription>
                Update the user details below.
              </DialogDescription>
            </DialogHeader>
            <div className="grid gap-4 py-4">
              <UserAccountFields
                form={form}
                passwordMode="optional"
                showAdminControls
              />
            </div>

            <DialogFooter>
              <DialogClose asChild>
                <Button variant="outline" disabled={mutation.isPending}>
                  Cancel
                </Button>
              </DialogClose>
              <LoadingButton type="submit" loading={mutation.isPending}>
                Save
              </LoadingButton>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export default EditUser
