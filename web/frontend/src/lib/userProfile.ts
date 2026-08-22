import { zodResolver } from "@hookform/resolvers/zod"
import type { Resolver } from "react-hook-form"
import { z } from "zod"

export const medicalProfessions = [
  "Doctor / physician",
  "Clinical researcher",
  "Nurse practitioner",
  "Physician associate / assistant",
  "Nurse",
  "Pharmacist",
  "Other health professional",
  "AI researcher",
  "Student / trainee",
  "Other",
] as const

export const resolveProfessionFormValues = (
  medicalProfession: string | null | undefined,
) => {
  const savedProfession = medicalProfession ?? ""
  const usesCustomProfession =
    savedProfession !== "" &&
    !medicalProfessions.some((option) => option === savedProfession)

  return {
    medical_profession: usesCustomProfession ? "Other" : savedProfession,
    medical_profession_other: usesCustomProfession ? savedProfession : "",
  }
}

export const resolveProfessionSubmitValue = (
  medicalProfession: string | undefined,
  medicalProfessionOther: string | undefined,
) =>
  medicalProfession === "Other"
    ? medicalProfessionOther?.trim() || null
    : medicalProfession?.trim() || null

type PasswordMode = "none" | "required" | "optional"

interface UserAccountFormSchemaOptions {
  passwordMode?: PasswordMode
  adminFields?: boolean
  requireProfession?: boolean
}

const emailSchema = z.email({ message: "Invalid email address" })
const optionalTextSchema = z.string().optional()
const professionSchema = z.string().max(100).optional()

export const createUserAccountFormSchema = ({
  passwordMode = "none",
  adminFields = false,
  requireProfession = false,
}: UserAccountFormSchemaOptions = {}) => {
  const schema = z.object({
    email: emailSchema,
    full_name: optionalTextSchema,
    discord_handle: z.string().max(255).optional(),
    medical_profession: requireProfession
      ? z.string().min(1, { message: "Profession is required" })
      : professionSchema,
    medical_profession_other: z.string().max(100).optional(),
    ...(passwordMode === "required"
      ? {
          password: z
            .string()
            .min(1, { message: "Password is required" })
            .min(8, { message: "Password must be at least 8 characters" }),
          confirm_password: z
            .string()
            .min(1, { message: "Please confirm your password" }),
        }
      : {}),
    ...(passwordMode === "optional"
      ? {
          password: z
            .string()
            .min(8, { message: "Password must be at least 8 characters" })
            .optional()
            .or(z.literal("")),
          confirm_password: optionalTextSchema,
        }
      : {}),
    ...(adminFields
      ? {
          is_superuser: z.boolean(),
          is_active: z.boolean(),
        }
      : {}),
  })

  return schema
    .refine(
      (data) =>
        data.medical_profession !== "Other" ||
        Boolean(data.medical_profession_other?.trim()),
      {
        message: "Profession title is required",
        path: ["medical_profession_other"],
      },
    )
    .refine(
      (data) =>
        passwordMode !== "optional" ||
        !data.password ||
        Boolean(data.confirm_password),
      {
        message: "Please confirm your password",
        path: ["confirm_password"],
      },
    )
    .refine(
      (data) =>
        passwordMode === "none" ||
        !data.password ||
        data.password === data.confirm_password,
      {
        message: "The passwords don't match",
        path: ["confirm_password"],
      },
    )
}

export const createUserAccountFormResolver = <
  TFormData extends UserAccountFormData,
>(
  options: UserAccountFormSchemaOptions = {},
): Resolver<TFormData, unknown, TFormData> =>
  zodResolver(createUserAccountFormSchema(options)) as unknown as Resolver<
    TFormData,
    unknown,
    TFormData
  >

export type UserAccountFormData = {
  email: string
  full_name?: string
  discord_handle?: string
  medical_profession: string | undefined
  medical_profession_other?: string
  password?: string
  confirm_password?: string
  is_superuser?: boolean
  is_active?: boolean
}

export const buildUserProfilePayload = (data: UserAccountFormData) => ({
  email: data.email,
  full_name: data.full_name?.trim() || null,
  discord_handle: data.discord_handle?.trim() || null,
  medical_profession: resolveProfessionSubmitValue(
    data.medical_profession ?? undefined,
    data.medical_profession_other ?? undefined,
  ),
})

export const buildAdminUserPayload = (data: UserAccountFormData) => ({
  ...buildUserProfilePayload(data),
  is_superuser: data.is_superuser,
  is_active: data.is_active,
})

export const buildPasswordPayload = (data: UserAccountFormData) =>
  data.password ? { password: data.password } : {}
