import { Moon, Sun } from "lucide-react"

import { Button } from "@/components/ui/button"
import useTheme from "@/hooks/useTheme"

export default function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggleTheme } = useTheme()
  const label =
    theme === "dark" ? "Switch to light mode" : "Switch to dark mode"
  return (
    <Button
      aria-label={label}
      className={className}
      data-testid="theme-toggle"
      onClick={toggleTheme}
      size="icon"
      title={label}
      type="button"
      variant="outline"
    >
      {theme === "dark" ? <Sun /> : <Moon />}
    </Button>
  )
}
