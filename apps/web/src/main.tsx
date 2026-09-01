import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter, HashRouter } from "react-router-dom"
import App from "./App"
import "./index.css"
import { isTauriRuntime, RuntimeProvider } from "./runtime"
import { ThemeProvider } from "./theme"

const Router = isTauriRuntime ? HashRouter : BrowserRouter

createRoot(document.getElementById("root")!).render(
  <StrictMode><Router><RuntimeProvider><ThemeProvider><App /></ThemeProvider></RuntimeProvider></Router></StrictMode>,
)
