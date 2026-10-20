import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AdminTemplateEditor } from "../AdminTemplateEditor";
import type { TemplateOption } from "../../services/templates";

vi.mock("../VideoPreview", () => ({
  VideoPreview: () => <div>Video preview</div>,
}));
afterEach(cleanup);

const template: TemplateOption = {
  id: "a".repeat(32),
  title: "Test scene",
  category: "Cinematic",
  category_id: "cinematic",
  video: "/test.mp4",
  poster: "/test.jpg",
  duration: 5,
  width: 640,
  height: 480,
  revision: 0,
  status: "draft",
  anchor: { x: 0.5, y: 0.4, time: 0 },
  face_track: [],
  tracking: "selected",
};

it("requires a person-selection review before publishing a prepared template", () => {
  const save = vi.fn();
  const track = vi.fn();
  render(
    <AdminTemplateEditor
      template={template}
      categories={[{ id: "cinematic", name: "Cinematic" }]}
      disabled={false}
      canEdit
      onSave={save}
      onTrack={track}
      onClose={() => {}}
    />,
  );
  expect(
    screen.getByRole("button", { name: "Publish template" }),
  ).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Track selected face" }));
  expect(track).toHaveBeenCalledWith(template);
  fireEvent.click(
    screen.getByRole("checkbox", { name: /reviewed the person/ }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Publish template" }));
  expect(save).toHaveBeenCalledWith(template, "published", true);
});

it("clearing the person returns a template to the standard Clip flow", () => {
  const save = vi.fn();
  render(
    <AdminTemplateEditor
      template={template}
      categories={[{ id: "cinematic", name: "Cinematic" }]}
      disabled={false}
      canEdit
      onSave={save}
      onTrack={() => {}}
      onClose={() => {}}
    />,
  );
  fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
  expect(
    screen.getByText("No person selected. This template will start at Clip."),
  ).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Publish template" }));
  expect(save).toHaveBeenCalledWith(
    expect.objectContaining({ anchor: null, face_track: [], tracking: "none" }),
    "published",
    false,
  );
});
