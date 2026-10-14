import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { CharSwapWalkthrough } from "./index";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <CharSwapWalkthrough />
  </StrictMode>,
);
