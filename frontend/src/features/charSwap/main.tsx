import { AppErrorBoundary } from "./components/AppErrorBoundary";
import { installErrorReporting } from "./services/diagnostics";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { SwapMeinApp } from "./SwapMeinApp";

installErrorReporting();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <AppErrorBoundary><SwapMeinApp /></AppErrorBoundary>
  </StrictMode>,
);
