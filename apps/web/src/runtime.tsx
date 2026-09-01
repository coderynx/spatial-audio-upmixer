import * as React from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";

const SERVER_URL_KEY = "upmixer.server-url";
const DEFAULT_SERVER_URL = "http://127.0.0.1:8000";

export type NativeCapabilities = {
  nativeDsp: boolean;
  appleSpatial: boolean;
  maxChannels: number;
  error: string | null;
};

export const isTauriRuntime = isTauri();

export function normalizeServerUrl(value: string): string {
  const url = new URL(value.trim());
  if (!/^https?:$/.test(url.protocol)) throw new Error("Use an HTTP or HTTPS URL.");
  if (url.username || url.password) throw new Error("Credentials are not allowed in the processing-node URL.");
  if (url.search || url.hash) throw new Error("Remove the query string and fragment from the processing-node URL.");
  return url.toString().replace(/\/$/, "");
}

export function getServerUrl(): string {
  if (!isTauriRuntime) return "";
  try {
    return normalizeServerUrl(window.localStorage.getItem(SERVER_URL_KEY) || DEFAULT_SERVER_URL);
  } catch {
    return DEFAULT_SERVER_URL;
  }
}

export function saveServerUrl(value: string): string {
  const normalized = normalizeServerUrl(value);
  window.localStorage.setItem(SERVER_URL_KEY, normalized);
  return normalized;
}

const BROWSER_CAPABILITIES: NativeCapabilities = {
  nativeDsp: false,
  appleSpatial: false,
  maxChannels: 2,
  error: null,
};

const RuntimeContext = React.createContext<NativeCapabilities>(BROWSER_CAPABILITIES);

export function RuntimeProvider({ children }: { children: React.ReactNode }) {
  const [capabilities, setCapabilities] = React.useState(BROWSER_CAPABILITIES);

  React.useEffect(() => {
    if (!isTauriRuntime) return;
    invoke<NativeCapabilities>("native_capabilities")
      .then(setCapabilities)
      .catch((error) => setCapabilities({
        ...BROWSER_CAPABILITIES,
        error: error instanceof Error ? error.message : String(error),
      }));
  }, []);

  return <RuntimeContext.Provider value={capabilities}>{children}</RuntimeContext.Provider>;
}

export function useRuntime(): NativeCapabilities & { isTauri: boolean; serverUrl: string } {
  return { ...React.useContext(RuntimeContext), isTauri: isTauriRuntime, serverUrl: getServerUrl() };
}
