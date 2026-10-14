import { useMemo, useState } from "react";
import type { ReactNode } from "react";
import { ThemeProvider } from "@mui/material/styles";
import { CssBaseline, GlobalStyles } from "@mui/material";
import { createSwapTheme } from "./theme";
import type { ColorMode } from "./theme";
import { ModeContext } from "./themeContext";

export function SwapThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setMode] = useState<ColorMode>(() => {
    try {
      return localStorage.getItem("swapmein-color-mode") === "dark"
        ? "dark"
        : "light";
    } catch {
      return "light";
    }
  });
  const theme = useMemo(() => createSwapTheme(mode), [mode]);
  function toggle() {
    const next = mode === "light" ? "dark" : "light";
    setMode(next);
    try {
      localStorage.setItem("swapmein-color-mode", next);
    } catch {
      /* The theme still works without storage. */
    }
  }
  return (
    <ModeContext.Provider value={{ mode, toggle, theme }}>
      <ThemeProvider theme={theme}>
        <CssBaseline />
        <GlobalStyles
          styles={{
            html: { colorScheme: mode, scrollBehavior: "smooth" },
            body: { margin: 0, overflowX: "hidden" },
            "*": { boxSizing: "border-box" },
            "::selection": { background: "#f6b0a8", color: "#252326" },
            "button:focus-visible, a:focus-visible": {
              outline: "2px solid #dc302f",
              outlineOffset: 4,
            },
            "@media (prefers-reduced-motion: reduce)": {
              "*": {
                scrollBehavior: "auto !important",
                transition: "none !important",
                animation: "none !important",
              },
            },
          }}
        />
        {children}
      </ThemeProvider>
    </ModeContext.Provider>
  );
}
