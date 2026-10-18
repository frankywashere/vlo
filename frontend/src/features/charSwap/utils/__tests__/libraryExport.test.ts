import { expect, it } from "vitest";
import { libraryExport } from "../libraryExport";

it("exports lasting generation metadata without private signed media links", () => {
  const output = libraryExport([
    {
      id: "job",
      title: "My clip",
      status: "completed",
      created_at: 100,
      duration: 5,
      output_url: "https://example.com/private?token=secret",
      source_preview_url: "https://example.com/upload",
      reference_url: "https://example.com/character",
    },
  ]);
  expect(JSON.parse(output).generations).toEqual([
    {
      id: "job",
      title: "My clip",
      status: "completed",
      created_at: 100,
      duration_seconds: 5,
    },
  ]);
  expect(output).not.toContain("https://");
  expect(output).not.toContain("secret");
});
