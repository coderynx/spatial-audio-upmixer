import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/theme";

export function ThemeToggle() {
  const { theme, setTheme } = useTheme();
  const effective =
    theme === "dark"
      ? "dark"
      : theme === "light"
        ? "light"
        : window.matchMedia("(prefers-color-scheme: dark)").matches
          ? "dark"
          : "light";
  return (
    <Button
      variant="ghost"
      size="icon"
      aria-label="Toggle theme"
      onClick={() => setTheme(effective === "dark" ? "light" : "dark")}
    >
      {effective === "dark" ? <Sun /> : <Moon />}
    </Button>
  );
}
