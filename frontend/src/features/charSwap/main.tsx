import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { SwapMeinApp } from "./SwapMeinApp";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <SwapMeinApp />
  </StrictMode>,
);
