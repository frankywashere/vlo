import { createTheme } from "@mui/material/styles";

function createSwapMeinTheme(mode: "light" | "dark") {
  const dark = mode === "dark";
  return createTheme({
    palette: {
      mode,
      primary: {
        main: dark ? "#ff8177" : "#cc3f37",
        dark: dark ? "#e66359" : "#ac2d27",
        light: dark ? "#ffada5" : "#e97970",
        contrastText: dark ? "#281513" : "#ffffff",
      },
      background: {
        default: dark ? "#141316" : "#ffffff",
        paper: dark ? "#201e22" : "#faf8f7",
      },
      text: {
        primary: dark ? "#f6f3f0" : "#252326",
        secondary: dark ? "#b6afb8" : "#716b73",
      },
      divider: dark ? "#38323b" : "#eae6e5",
      action: {
        hover: dark ? "#2a2226" : "#fff6f3",
        selected: dark ? "#3b272b" : "#fff0ed",
      },
      success: { main: dark ? "#7acfa0" : "#267347" },
    },
    typography: {
      fontFamily:
        '"Segoe UI", Inter, -apple-system, BlinkMacSystemFont, sans-serif',
      h1: { fontWeight: 800, letterSpacing: "-.065em", lineHeight: 1.02 },
      h2: { fontWeight: 750, letterSpacing: "-.05em", lineHeight: 1.1 },
      h4: { fontWeight: 700, letterSpacing: "-.045em" },
      h6: { fontWeight: 650, letterSpacing: "-.025em" },
      button: { textTransform: "none", fontWeight: 650 },
      body1: { lineHeight: 1.7 },
      body2: { lineHeight: 1.65 },
    },
    shape: { borderRadius: 5 },
    components: {
      MuiButton: {
        defaultProps: { disableElevation: true },
        styleOverrides: {
          root: { minHeight: 44, borderRadius: 5, paddingInline: 20 },
        },
      },
      MuiIconButton: { styleOverrides: { root: { borderRadius: 5 } } },
      MuiChip: {
        styleOverrides: { root: { borderRadius: 5, fontWeight: 600 } },
      },
      MuiDialog: {
        styleOverrides: { paper: { backgroundImage: "none", borderRadius: 8 } },
      },
      MuiSlider: {
        styleOverrides: {
          thumb: { width: 18, height: 18 },
          root: { padding: "16px 0" },
        },
      },
    },
  });
}

export const swapMeinTheme = createSwapMeinTheme("light");
export const swapMeinDarkTheme = createSwapMeinTheme("dark");
