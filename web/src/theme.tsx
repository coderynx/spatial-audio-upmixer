import * as React from "react"

type Theme = "dark" | "light" | "system"
type ThemeContextValue = { theme: Theme; setTheme: (theme: Theme) => void }
const ThemeContext = React.createContext<ThemeContextValue | undefined>(undefined)

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const [theme, setTheme] = React.useState<Theme>(() => (localStorage.getItem("upmixer-theme") as Theme) || "system")
  React.useEffect(() => {
    const root = document.documentElement
    root.classList.remove("light", "dark")
    const effective = theme === "system" ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light") : theme
    root.classList.add(effective)
    localStorage.setItem("upmixer-theme", theme)
  }, [theme])
  return <ThemeContext.Provider value={{ theme, setTheme }}>{children}</ThemeContext.Provider>
}

export function useTheme() {
  const value = React.useContext(ThemeContext)
  if (!value) throw new Error("useTheme must be used inside ThemeProvider")
  return value
}
