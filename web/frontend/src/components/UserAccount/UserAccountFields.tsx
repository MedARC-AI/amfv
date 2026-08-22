import type { UseFormReturn } from "react-hook-form"

import { Checkbox } from "@/components/ui/checkbox"
import {
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { PasswordInput } from "@/components/ui/password-input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { medicalProfessions } from "@/lib/userProfile"
import { cn } from "@/lib/utils"

export type UserAccountFormValues = {
  email?: string
  full_name?: string | null
  discord_handle?: string | null
  medical_profession?: string | null
  medical_profession_other?: string | null
  password?: string | null
  confirm_password?: string | null
  is_superuser?: boolean
  is_active?: boolean
}

type PasswordMode = "none" | "required" | "optional"

interface UserAccountFieldsProps<TFormValues extends UserAccountFormValues> {
  form: UseFormReturn<TFormValues, unknown, TFormValues>
  readOnly?: boolean
  passwordMode?: PasswordMode
  showAdminControls?: boolean
  requireProfession?: boolean
  messageClassName?: string
}

const displayValue = (value: unknown) =>
  typeof value === "string" && value.trim() ? value : "N/A"

export function UserAccountFields<TFormValues extends UserAccountFormValues>({
  form,
  readOnly = false,
  passwordMode = "none",
  showAdminControls = false,
  requireProfession = false,
  messageClassName,
}: UserAccountFieldsProps<TFormValues>) {
  const profession = form.watch("medical_profession" as never) as unknown as
    | string
    | undefined

  return (
    <>
      <FormField
        control={form.control}
        name={"email" as never}
        render={({ field }) =>
          readOnly ? (
            <FormItem>
              <FormLabel>Email</FormLabel>
              <p className="py-2 truncate max-w-sm">{field.value}</p>
            </FormItem>
          ) : (
            <FormItem>
              <FormLabel>
                Email <span className="text-destructive">*</span>
              </FormLabel>
              <FormControl>
                <Input
                  placeholder="user@example.com"
                  type="email"
                  {...field}
                  value={field.value ?? ""}
                  required
                />
              </FormControl>
              <FormMessage className={messageClassName} />
            </FormItem>
          )
        }
      />

      <FormField
        control={form.control}
        name={"full_name" as never}
        render={({ field }) =>
          readOnly ? (
            <FormItem>
              <FormLabel>Name</FormLabel>
              <p
                className={cn(
                  "py-2 truncate max-w-sm",
                  !field.value && "text-muted-foreground",
                )}
              >
                {displayValue(field.value)}
              </p>
            </FormItem>
          ) : (
            <FormItem>
              <FormLabel>Name</FormLabel>
              <FormControl>
                <Input
                  placeholder="Full name"
                  type="text"
                  {...field}
                  value={field.value ?? ""}
                />
              </FormControl>
              <FormMessage className={messageClassName} />
            </FormItem>
          )
        }
      />

      <FormField
        control={form.control}
        name={"discord_handle" as never}
        render={({ field }) =>
          readOnly ? (
            <FormItem>
              <FormLabel>Discord handle</FormLabel>
              <p
                className={cn(
                  "py-2 truncate max-w-sm",
                  !field.value && "text-muted-foreground",
                )}
              >
                {displayValue(field.value)}
              </p>
            </FormItem>
          ) : (
            <FormItem>
              <FormLabel>Discord handle</FormLabel>
              <FormControl>
                <Input
                  placeholder="username"
                  type="text"
                  {...field}
                  value={field.value ?? ""}
                />
              </FormControl>
              <FormMessage className={messageClassName} />
            </FormItem>
          )
        }
      />

      <FormField
        control={form.control}
        name={"medical_profession" as never}
        render={({ field }) =>
          readOnly ? (
            <FormItem>
              <FormLabel>Profession</FormLabel>
              <p
                className={cn(
                  "py-2 truncate max-w-sm",
                  !field.value && "text-muted-foreground",
                )}
              >
                {field.value === "Other"
                  ? displayValue(
                      form.getValues("medical_profession_other" as never),
                    )
                  : displayValue(field.value)}
              </p>
            </FormItem>
          ) : (
            <FormItem>
              <FormLabel>
                Profession{" "}
                {requireProfession ? (
                  <span className="text-destructive">*</span>
                ) : null}
              </FormLabel>
              <Select
                onValueChange={field.onChange}
                value={(field.value as string | undefined) ?? ""}
              >
                <FormControl>
                  <SelectTrigger>
                    <SelectValue placeholder="Select profession" />
                  </SelectTrigger>
                </FormControl>
                <SelectContent>
                  {medicalProfessions.map((option) => (
                    <SelectItem key={option} value={option}>
                      {option}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <FormMessage className={messageClassName} />
            </FormItem>
          )
        }
      />

      {!readOnly && profession === "Other" ? (
        <FormField
          control={form.control}
          name={"medical_profession_other" as never}
          render={({ field }) => (
            <FormItem>
              <FormLabel>
                Other profession{" "}
                {requireProfession ? (
                  <span className="text-destructive">*</span>
                ) : null}
              </FormLabel>
              <FormControl>
                <Input
                  placeholder="Profession title"
                  type="text"
                  {...field}
                  value={field.value ?? ""}
                />
              </FormControl>
              <FormMessage className={messageClassName} />
            </FormItem>
          )}
        />
      ) : null}

      {passwordMode !== "none" ? (
        <>
          <FormField
            control={form.control}
            name={"password" as never}
            render={({ field }) => (
              <FormItem>
                <FormLabel>
                  {passwordMode === "optional" ? "Set Password" : "Password"}{" "}
                  {passwordMode === "required" ? (
                    <span className="text-destructive">*</span>
                  ) : null}
                </FormLabel>
                <FormControl>
                  <PasswordInput
                    placeholder="Password"
                    {...field}
                    value={field.value ?? ""}
                    required={passwordMode === "required"}
                  />
                </FormControl>
                <FormMessage className={messageClassName} />
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name={"confirm_password" as never}
            render={({ field }) => (
              <FormItem>
                <FormLabel>
                  Confirm Password{" "}
                  {passwordMode === "required" ? (
                    <span className="text-destructive">*</span>
                  ) : null}
                </FormLabel>
                <FormControl>
                  <PasswordInput
                    placeholder="Password"
                    {...field}
                    value={field.value ?? ""}
                    required={passwordMode === "required"}
                  />
                </FormControl>
                <FormMessage className={messageClassName} />
              </FormItem>
            )}
          />
        </>
      ) : null}

      {showAdminControls ? (
        <>
          <FormField
            control={form.control}
            name={"is_superuser" as never}
            render={({ field }) => (
              <FormItem className="flex items-center gap-3 space-y-0">
                <FormControl>
                  <Checkbox
                    checked={Boolean(field.value)}
                    onCheckedChange={field.onChange}
                  />
                </FormControl>
                <FormLabel className="font-normal">Is superuser?</FormLabel>
              </FormItem>
            )}
          />

          <FormField
            control={form.control}
            name={"is_active" as never}
            render={({ field }) => (
              <FormItem className="flex items-center gap-3 space-y-0">
                <FormControl>
                  <Checkbox
                    checked={Boolean(field.value)}
                    onCheckedChange={field.onChange}
                  />
                </FormControl>
                <FormLabel className="font-normal">Is active?</FormLabel>
              </FormItem>
            )}
          />
        </>
      ) : null}
    </>
  )
}
