import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const directory = resolve(root, "deployment/swapmein");
const python = resolve(
  directory,
  process.platform === "win32"
    ? ".venv/Scripts/python.exe"
    : ".venv/bin/python",
);
if (!existsSync(python))
  throw new Error(
    "Install the template gateway requirements into deployment/swapmein/.venv first.",
  );
const child = spawn(
  python,
  [
    "-m",
    "uvicorn",
    "swapmein.app:app",
    "--host",
    "127.0.0.1",
    "--port",
    "8081",
    "--workers",
    "1",
    "--no-access-log",
  ],
  {
    cwd: directory,
    stdio: "inherit",
    windowsHide: true,
    env: {
      ...process.env,
      TEMPLATE_LOCAL_DIRECTORY: resolve(
        root,
        "backend/runtime/template-library",
      ),
      ALLOWED_ORIGINS: "https://recastfx.com,http://localhost,http://127.0.0.1",
      REQUIRE_ACCOUNT: "true",
    },
  },
);
child.on("error", (error) => {
  process.stderr.write(error.message + "\n");
  process.exitCode = 1;
});
child.on("exit", (code) => {
  process.exitCode = code ?? 1;
});
process.on("SIGINT", () => child.kill("SIGINT"));
process.on("SIGTERM", () => child.kill("SIGTERM"));
