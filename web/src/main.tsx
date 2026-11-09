import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { Theme } from "@astryxdesign/core/theme";
import "@astryxdesign/core/reset.css";
import "@astryxdesign/core/astryx.css";
import { neutralTheme } from "@astryxdesign/theme-neutral/built";
import "@astryxdesign/theme-neutral/theme.css";
import "@fontsource-variable/dm-sans";
import "@fontsource-variable/manrope";
import { App } from "./App";
import "./style.css";
import "./shadcn.css";
import "./run-timeline.css";

const root = document.getElementById("root");
if (!root) throw new Error("Application root missing");
function Application() {
  const [mode, setMode] = useState<"light" | "dark">(
    document.documentElement.dataset.theme === "dark" ? "dark" : "light",
  );
  const changeMode = () => {
    const next = mode === "dark" ? "light" : "dark";
    const query = new URLSearchParams(window.location.search);
    query.set("scoutTheme", next);
    window.history.replaceState(null, "", `?${query}`);
    setMode(next);
  };
  return <Theme theme={neutralTheme} mode={mode}><App mode={mode} changeMode={changeMode} /></Theme>;
}
createRoot(root).render(
  <StrictMode>
    <Application />
  </StrictMode>,
);
