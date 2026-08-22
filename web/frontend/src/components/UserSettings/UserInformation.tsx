import { useMutation, useQueryClient } from "@tanstack/react-query"
import { useState } from "react"
import { useForm } from "react-hook-form"

import { UsersService, type UserUpdateMe } from "@/client"
import { UserAccountFields } from "@/components/UserAccount/UserAccountFields"
import { Button } from "@/components/ui/button"
import { Form } from "@/components/ui/form"
import { LoadingButton } from "@/components/ui/loading-button"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"
import {
  buildUserProfilePayload,
  createUserAccountFormResolver,
  resolveProfessionFormValues,
  type UserAccountFormData,
} from "@/lib/userProfile"
import { handleError } from "@/utils"

type FormData = UserAccountFormData

const UserInformation = () => {
  const queryClient = useQueryClient()
  const { showSuccessToast, showErrorToast } = useCustomToast()
  const [editMode, setEditMode] = useState(false)
  const { user: currentUser } = useAuth()
  const professionValues = resolveProfessionFormValues(
    currentUser?.medical_profession,
  )

  const form = useForm<FormData, unknown, FormData>({
    resolver: createUserAccountFormResolver<FormData>(),
    mode: "onBlur",
    criteriaMode: "all",
    defaultValues: {
      full_name: currentUser?.full_name ?? "",
      email: currentUser?.email ?? "",
      discord_handle: currentUser?.discord_handle ?? "",
      ...professionValues,
    },
  })

  const toggleEditMode = () => {
    setEditMode(!editMode)
  }

  const mutation = useMutation({
    mutationFn: (data: UserUpdateMe) =>
      UsersService.updateUserMe({ requestBody: data }),
    onSuccess: () => {
      showSuccessToast("User updated successfully")
      toggleEditMode()
    },
    onError: handleError.bind(showErrorToast),
    onSettled: () => {
      queryClient.invalidateQueries()
    },
  })

  const onSubmit = (data: FormData) => {
    const updateData: UserUpdateMe = {}
    const profilePayload = buildUserProfilePayload(data)

    if (profilePayload.full_name !== (currentUser?.full_name ?? null)) {
      updateData.full_name = profilePayload.full_name
    }
    if (profilePayload.email !== currentUser?.email) {
      updateData.email = profilePayload.email
    }
    if (
      profilePayload.discord_handle !== (currentUser?.discord_handle ?? null)
    ) {
      updateData.discord_handle = profilePayload.discord_handle
    }
    if (
      profilePayload.medical_profession !==
      (currentUser?.medical_profession ?? null)
    ) {
      updateData.medical_profession = profilePayload.medical_profession
    }

    mutation.mutate(updateData)
  }

  const onCancel = () => {
    form.reset()
    toggleEditMode()
  }

  return (
    <div className="max-w-md">
      <h3 className="text-lg font-semibold py-4">User Information</h3>
      <Form {...form}>
        <form
          onSubmit={form.handleSubmit(onSubmit)}
          className="flex flex-col gap-4"
        >
          <UserAccountFields form={form} readOnly={!editMode} />

          <div className="flex gap-3">
            {editMode ? (
              <>
                <LoadingButton
                  type="submit"
                  loading={mutation.isPending}
                  disabled={!form.formState.isDirty}
                >
                  Save
                </LoadingButton>
                <Button
                  type="button"
                  variant="outline"
                  onClick={onCancel}
                  disabled={mutation.isPending}
                >
                  Cancel
                </Button>
              </>
            ) : (
              <Button type="button" onClick={toggleEditMode}>
                Edit
              </Button>
            )}
          </div>
        </form>
      </Form>
    </div>
  )
}

export default UserInformation
