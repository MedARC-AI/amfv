import {
  useMutation,
  useQuery,
  useQueryClient,
  useSuspenseQuery,
} from "@tanstack/react-query"
import { Link, UserPlus } from "lucide-react"
import { Suspense, useMemo, useState } from "react"

import {
  AuthService,
  type SignupInviteCreated,
  type SignupInvitePublic,
  type UserPublic,
  type UserRole,
  UsersService,
} from "@/client"
import AddUser from "@/components/Admin/AddUser"
import { columns, type UserTableData } from "@/components/Admin/columns"
import { DataTable } from "@/components/Common/DataTable"
import PendingUsers from "@/components/Pending/PendingUsers"
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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import useAuth from "@/hooks/useAuth"
import useCustomToast from "@/hooks/useCustomToast"

const roleLabels: Record<UserRole, string> = {
  user: "User",
  data_admin: "Data admin",
  admin: "Admin",
}

type InviteStatus = {
  label: string
  variant: "default" | "secondary" | "destructive" | "outline"
  isActive: boolean
}

function getInviteStatus(invite: SignupInvitePublic): InviteStatus {
  if (invite.disabled_at) {
    return { label: "Disabled", variant: "outline", isActive: false }
  }
  if (invite.expires_at && new Date(invite.expires_at) <= new Date()) {
    return { label: "Expired", variant: "outline", isActive: false }
  }
  if (invite.redeemed_count >= (invite.max_redemptions ?? 1)) {
    return { label: "Used up", variant: "secondary", isActive: false }
  }
  return { label: "Active", variant: "default", isActive: true }
}

function getInvitesQueryOptions() {
  return {
    queryFn: () => AuthService.listInvites(),
    queryKey: ["invites"],
  }
}

const inviteTimeoutOptions = [
  { label: "Never", value: "never", milliseconds: null },
  { label: "1 hour", value: "1h", milliseconds: 60 * 60 * 1000 },
  { label: "24 hours", value: "24h", milliseconds: 24 * 60 * 60 * 1000 },
  { label: "7 days", value: "7d", milliseconds: 7 * 24 * 60 * 60 * 1000 },
  { label: "30 days", value: "30d", milliseconds: 30 * 24 * 60 * 60 * 1000 },
] as const

type InviteTimeoutValue = (typeof inviteTimeoutOptions)[number]["value"]

function getPublicAppUrl() {
  const configuredUrl = import.meta.env.VITE_PUBLIC_APP_URL?.trim()
  return (configuredUrl || window.location.origin).replace(/\/+$/, "")
}

function inviteExpiresAt(timeout: InviteTimeoutValue): string | null {
  const option = inviteTimeoutOptions.find((item) => item.value === timeout)
  if (!option?.milliseconds) {
    return null
  }
  return new Date(Date.now() + option.milliseconds).toISOString()
}

function getUsersQueryOptions() {
  return {
    queryFn: () => UsersService.readUsers({ skip: 0, limit: 100 }),
    queryKey: ["users"],
  }
}

function UsersTableContent() {
  const { user: currentUser } = useAuth()
  const { data: users } = useSuspenseQuery(getUsersQueryOptions())

  const tableData: UserTableData[] = users.data.map((user: UserPublic) => ({
    ...user,
    isCurrentUser: currentUser?.id === user.id,
  }))

  return <DataTable columns={columns} data={tableData} />
}

function UsersTable() {
  return (
    <Suspense fallback={<PendingUsers />}>
      <UsersTableContent />
    </Suspense>
  )
}

function InvitePanel() {
  const [role, setRole] = useState<UserRole>("user")
  const [maxRedemptions, setMaxRedemptions] = useState(1)
  const [timeout, setInviteTimeout] = useState<InviteTimeoutValue>("never")
  const [invite, setInvite] = useState<SignupInviteCreated | null>(null)
  const [copied, setCopied] = useState(false)

  const queryClient = useQueryClient()
  const { showErrorToast } = useCustomToast()

  const inviteLink = useMemo(() => {
    if (!invite) return ""
    return `${getPublicAppUrl()}/signup#token=${encodeURIComponent(invite.token)}`
  }, [invite])

  const { data: invites } = useQuery(getInvitesQueryOptions())

  const createInvite = useMutation({
    mutationFn: () =>
      AuthService.createInvite({
        requestBody: {
          role,
          expires_at: inviteExpiresAt(timeout),
          max_redemptions: maxRedemptions,
        },
      }),
    onSuccess: (created) => {
      setInvite(created)
      setCopied(false)
      queryClient.invalidateQueries({ queryKey: ["invites"] })
    },
  })

  const disableInvite = useMutation({
    mutationFn: (inviteId: string) => AuthService.disableInvite({ inviteId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["invites"] })
    },
    onError: () => {
      showErrorToast("Could not disable invite")
    },
  })

  const copyInvite = async () => {
    if (!inviteLink) return
    await navigator.clipboard.writeText(inviteLink)
    setCopied(true)
  }

  return (
    <section className="rounded-md border p-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="flex items-center gap-3">
          <UserPlus className="text-muted-foreground size-5" />
          <div>
            <h2 className="text-base font-semibold tracking-normal">
              Invite link
            </h2>
            <p className="text-muted-foreground text-sm">
              Generate a controlled signup link for a new user.
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-end gap-4">
          <div className="grid w-32 gap-2">
            <Label>Role</Label>
            <Select
              onValueChange={(value: UserRole) => setRole(value)}
              value={role}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="user">User</SelectItem>
                <SelectItem value="data_admin">Data admin</SelectItem>
                <SelectItem value="admin">Admin</SelectItem>
              </SelectContent>
            </Select>
          </div>

          <div className="grid w-24 max-w-full gap-2">
            <Label htmlFor="invite-redemptions">Uses</Label>
            <Input
              id="invite-redemptions"
              min={1}
              max={100}
              onChange={(event) =>
                setMaxRedemptions(Number(event.target.value) || 1)
              }
              type="number"
              value={maxRedemptions}
            />
          </div>

          <div className="grid w-36 gap-2">
            <Label>Expires</Label>
            <Select
              onValueChange={(value: InviteTimeoutValue) =>
                setInviteTimeout(value)
              }
              value={timeout}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {inviteTimeoutOptions.map((option) => (
                  <SelectItem key={option.value} value={option.value}>
                    {option.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="flex items-end">
            <Button
              className="whitespace-nowrap"
              disabled={createInvite.isPending}
              onClick={() => createInvite.mutate()}
              type="button"
            >
              Create invite
            </Button>
          </div>
        </div>
      </div>

      {createInvite.isError ? (
        <p className="text-destructive mt-3 text-sm">
          Could not create invite.
        </p>
      ) : null}

      {inviteLink ? (
        <div className="mt-4 grid gap-2">
          <Label htmlFor="invite-link">Signup link</Label>
          <div className="flex gap-2">
            <Input id="invite-link" readOnly value={inviteLink} />
            <Button onClick={copyInvite} type="button" variant="outline">
              <Link className="size-4" />
              {copied ? "Copied" : "Copy"}
            </Button>
          </div>
          <p className="text-muted-foreground text-sm">
            {invite?.expires_at
              ? `Expires ${new Date(invite.expires_at).toLocaleString()}`
              : "Does not expire by time"}
          </p>
        </div>
      ) : null}

      {invites && invites.data.length > 0 ? (
        <div className="mt-6">
          <h3 className="mb-2 text-sm font-semibold">Existing invites</h3>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Role</TableHead>
                  <TableHead>Created</TableHead>
                  <TableHead>Uses</TableHead>
                  <TableHead>Expires</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {invites.data.map((item) => {
                  const status = getInviteStatus(item)
                  return (
                    <TableRow key={item.id}>
                      <TableCell>{roleLabels[item.role ?? "user"]}</TableCell>
                      <TableCell className="text-muted-foreground">
                        {item.created_at
                          ? new Date(item.created_at).toLocaleString()
                          : "—"}
                      </TableCell>
                      <TableCell>
                        {item.redeemed_count} / {item.max_redemptions ?? 1}
                      </TableCell>
                      <TableCell className="text-muted-foreground">
                        {item.expires_at
                          ? new Date(item.expires_at).toLocaleString()
                          : "Never"}
                      </TableCell>
                      <TableCell>
                        <Badge variant={status.variant}>{status.label}</Badge>
                      </TableCell>
                      <TableCell className="text-right">
                        {status.isActive ? (
                          <Button
                            disabled={disableInvite.isPending}
                            onClick={() => disableInvite.mutate(item.id)}
                            size="sm"
                            type="button"
                            variant="outline"
                          >
                            Disable
                          </Button>
                        ) : null}
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </div>
        </div>
      ) : null}
    </section>
  )
}

export default function UsersAdmin() {
  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Users</h1>
          <p className="text-muted-foreground">
            Manage user accounts and permissions
          </p>
        </div>
        <AddUser />
      </div>
      <InvitePanel />
      <UsersTable />
    </div>
  )
}
