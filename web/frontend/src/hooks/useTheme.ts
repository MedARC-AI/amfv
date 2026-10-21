import { useCallback, useEffect, useState } from "react"

type Theme = "light" | "dark"

/** Shared with the inline script in index.html that applies the theme before first paint. */
const THEME_STORAGE_KEY = "amfv-theme"

function readStoredTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY)
    return value === "dark" || value === "light" ? value : null
  } catch {
    return null
  }
}

function systemTheme(): Theme {
  return window.matchMedia?.("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light"
}

function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark")
}

export default function useTheme() {
  const [theme, setThemeState] = useState<Theme>(
    () => readStoredTheme() ?? systemTheme(),
  )

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  const toggleTheme = useCallback(() => {
    const next: Theme = theme === "dark" ? "light" : "dark"
    setThemeState(next)
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next)
    } catch {
      // Private mode or blocked storage: the choice lasts for this page only.
    }
  }, [theme])

  return { theme, toggleTheme }
}
