import { ClipboardCheck, Home, PenLine, Rows3, Users } from "lucide-react"

import { Logo } from "@/components/Common/Logo"
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarHeader,
} from "@/components/ui/sidebar"
import useAuth from "@/hooks/useAuth"
import { type Item, Main } from "./Main"
import { User } from "./User"

const baseItems: Item[] = [
  { icon: Home, title: "Home", path: "/" },
  { icon: ClipboardCheck, title: "Review", path: "/review" },
  { icon: PenLine, title: "Create", path: "/create" },
  { icon: Rows3, title: "My Work", path: "/my-work" },
]

export function AppSidebar() {
  const { user: currentUser } = useAuth()
  const canUseAdmin =
    currentUser?.is_superuser ||
    currentUser?.role === "admin" ||
    currentUser?.role === "data_admin"

  const items = canUseAdmin
    ? [...baseItems, { icon: Users, title: "Admin", path: "/admin" }]
    : baseItems

  return (
    <Sidebar collapsible="icon">
      <SidebarHeader className="h-16 justify-center bg-primary px-4 group-data-[collapsible=icon]:px-0 group-data-[collapsible=icon]:items-center">
        <Logo variant="responsive" />
      </SidebarHeader>
      <SidebarContent>
        <Main items={items} />
      </SidebarContent>
      <SidebarFooter>
        <User user={currentUser} />
      </SidebarFooter>
    </Sidebar>
  )
}

export default AppSidebar
