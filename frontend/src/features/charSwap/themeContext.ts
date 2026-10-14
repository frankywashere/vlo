import { createContext, useContext } from "react";
import { createSwapTheme } from "./theme";
import type { ColorMode } from "./theme";

const defaultTheme = createSwapTheme("light");
export const ModeContext = createContext({
  mode: "light" as ColorMode,
  toggle: () => {},
  theme: defaultTheme,
});
export function useSwapTheme() {
  return useContext(ModeContext);
}
