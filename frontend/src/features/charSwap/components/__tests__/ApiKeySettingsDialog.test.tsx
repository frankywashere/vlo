import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DEMO_API_KEY } from "../../hooks/useApiKeyPreview";
import { ApiKeySettingsDialog } from "../ApiKeySettingsDialog";

afterEach(() => cleanup());

describe("API settings preview dialog", () => {
  it("blocks empty saves and saves trimmed arbitrary key text only when requested", () => {
    const onSave = vi.fn();
    const onClose = vi.fn();
    render(
      <ApiKeySettingsDialog
        open
        apiKey=""
        onSave={onSave}
        onClear={vi.fn()}
        onClose={onClose}
      />,
    );
    expect(
      screen.getByRole("dialog", { name: "Settings" }),
    ).toBeInTheDocument();
    const input = screen.getByLabelText("reAPI API key");
    const save = screen.getByRole("button", { name: "Save for preview" });
    expect(save).toBeDisabled();
    fireEvent.change(input, { target: { value: "   " } });
    expect(save).toBeDisabled();
    fireEvent.change(input, { target: { value: "  example key  " } });
    expect(onSave).not.toHaveBeenCalled();
    fireEvent.click(save);
    expect(onSave).toHaveBeenCalledExactlyOnceWith("example key");
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("discards cancelled drafts and reopens with the currently saved key", () => {
    const props = {
      apiKey: "saved-example",
      onSave: vi.fn(),
      onClear: vi.fn(),
      onClose: vi.fn(),
    };
    const { rerender } = render(<ApiKeySettingsDialog {...props} open />);
    fireEvent.change(screen.getByLabelText("reAPI API key"), {
      target: { value: "unsaved-draft" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    expect(props.onSave).not.toHaveBeenCalled();
    expect(props.onClose).toHaveBeenCalledOnce();
    rerender(<ApiKeySettingsDialog {...props} open={false} />);
    rerender(
      <ApiKeySettingsDialog {...props} apiKey="newly-saved-example" open />,
    );
    expect(screen.getByLabelText("reAPI API key")).toHaveValue(
      "newly-saved-example",
    );
    expect(screen.queryByDisplayValue("unsaved-draft")).not.toBeInTheDocument();
  });

  it("keeps keys masked, provides an accessible show control, and fills an explicitly fake sample", () => {
    const onSave = vi.fn();
    render(
      <ApiKeySettingsDialog
        open
        apiKey="example-memory-key"
        onSave={onSave}
        onClear={vi.fn()}
        onClose={vi.fn()}
      />,
    );
    const input = screen.getByLabelText("reAPI API key");
    expect(input).toHaveAttribute("type", "password");
    expect(input).toHaveAttribute("autocomplete", "off");
    fireEvent.click(screen.getByRole("button", { name: "Show API key" }));
    expect(input).toHaveAttribute("type", "text");
    expect(
      screen.getByRole("button", { name: "Hide API key" }),
    ).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: "Use sample key" }));
    expect(input).toHaveValue(DEMO_API_KEY);
    expect(input).toHaveAttribute("type", "password");
    expect(
      screen.getByText("This is a fake key for the walkthrough."),
    ).toBeInTheDocument();
    expect(onSave).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Save for preview" }));
    expect(onSave).toHaveBeenCalledWith(DEMO_API_KEY);
  });

  it("clears the saved key and removes the raw value from the input", () => {
    const onClear = vi.fn();
    render(
      <ApiKeySettingsDialog
        open
        apiKey="example-memory-key"
        onSave={vi.fn()}
        onClear={onClear}
        onClose={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Show API key" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear key" }));
    const input = screen.getByLabelText("reAPI API key");
    expect(onClear).toHaveBeenCalledOnce();
    expect(input).toHaveValue("");
    expect(input).toHaveAttribute("type", "password");
    expect(
      screen.queryByDisplayValue("example-memory-key"),
    ).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Save for preview" }),
    ).toBeDisabled();
  });
  it("changes masking without requiring a key and locks it during processing", () => {
    const onMaskingChange = vi.fn();
    const props = {
      open: true,
      apiKey: "",
      onSave: vi.fn(),
      onClear: vi.fn(),
      onClose: vi.fn(),
      onMaskingChange,
    };
    const { rerender } = render(
      <ApiKeySettingsDialog {...props} enhancedCharacterMasking />,
    );
    const toggle = screen.getByRole("switch", {
      name: "Use enhanced character masking",
    });
    expect(toggle).toBeChecked();
    fireEvent.click(toggle);
    expect(onMaskingChange).toHaveBeenCalledWith(false);
    rerender(
      <ApiKeySettingsDialog
        {...props}
        enhancedCharacterMasking={false}
        maskingLocked
      />,
    );
    expect(toggle).not.toBeChecked();
    expect(toggle).toBeDisabled();
    expect(props.onSave).not.toHaveBeenCalled();
  });
  it("clears an unsaved key when switching providers", () => {
    const onProviderChange = vi.fn();
    render(<ApiKeySettingsDialog open apiKey="example-reapi-key" onSave={vi.fn()} onClear={vi.fn()} onClose={vi.fn()} onProviderChange={onProviderChange} />);
    fireEvent.mouseDown(screen.getByRole("combobox", {name: "Generation provider"}));
    fireEvent.click(screen.getByRole("option", {name: "ToAPIs"}));
    expect(onProviderChange).toHaveBeenCalledWith("ToAPIs");
    expect(screen.getByLabelText("reAPI API key")).toHaveValue("");
  });

});
