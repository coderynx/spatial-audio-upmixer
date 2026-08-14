import * as React from "react"

type Theme = "dark" | "light" | "system"
type ThemeContextValue = { theme: Theme; setTheme: (theme: Theme) => void }
const ThemeContext = React.createContext<ThemeContextValue | undefined>(undefined)

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = React.useState<Theme>(() => (localStorage.getItem("upmixer-theme") as Theme) || "system")
  React.useEffect(() => {
    const root = document.documentElement
    const query = window.matchMedia("(prefers-color-scheme: dark)")
    const apply = () => {
      root.classList.remove("light", "dark")
      root.classList.add(theme === "system" ? (query.matches ? "dark" : "light") : theme)
    }
    apply()
    localStorage.setItem("upmixer-theme", theme)
    // On "system" the OS appearance can change while the app is open, so the
    // class has to follow it rather than only being resolved once on mount.
    if (theme !== "system") return
    query.addEventListener("change", apply)
    return () => query.removeEventListener("change", apply)
  }, [theme])
  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const value = React.useContext(ThemeContext)
  if (!value) throw new Error("useTheme must be used inside ThemeProvider")
  return value
}
