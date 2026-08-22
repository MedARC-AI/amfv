import { createFileRoute } from "@tanstack/react-router"

import ChangePassword from "@/components/UserSettings/ChangePassword"
import UserInformation from "@/components/UserSettings/UserInformation"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import useAuth from "@/hooks/useAuth"

const tabsConfig = [
  { value: "my-profile", title: "My profile", component: UserInformation },
  { value: "password", title: "Password", component: ChangePassword },
]

export const Route = createFileRoute("/_layout/settings")({
  component: UserSettings,
  head: () => ({
    meta: [
      {
        title: "Settings - AMFV Web",
      },
    ],
  }),
})

function UserSettings() {
  const { user: currentUser } = useAuth()
  if (!currentUser) {
    return null
  }

  return (
    <div className="flex flex-col gap-6">
      <p className="text-muted-foreground">
        Manage your account settings and preferences
      </p>

      <Tabs defaultValue="my-profile">
        <TabsList>
          {tabsConfig.map((tab) => (
            <TabsTrigger key={tab.value} value={tab.value}>
              {tab.title}
            </TabsTrigger>
          ))}
        </TabsList>
        {tabsConfig.map((tab) => (
          <TabsContent key={tab.value} value={tab.value}>
            <tab.component />
          </TabsContent>
        ))}
      </Tabs>
    </div>
  )
}
