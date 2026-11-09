import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it } from "vitest";
import { GenerationModelChoice } from "../GenerationModelChoice";
import { GenerationOptions } from "../GenerationOptions";
import { useCharSwapStore } from "../../useCharSwapStore";
import { getStepBlocker } from "../../model/model";

beforeEach(() => {
  useCharSwapStore.getState().setKling4Enabled(false);
  useCharSwapStore.getState().reset();
  useCharSwapStore.getState().loadClip({id: "clip", name: "clip.mp4", url: "blob:clip", duration: 40, width: 1280, height: 720, mode: "upload"});
});
afterEach(() => {cleanup(); useCharSwapStore.getState().setKling4Enabled(false); useCharSwapStore.getState().reset();});

it("hides Kling until access is enabled and retains the original trim limit", () => {
  render(<GenerationModelChoice />);
  expect(screen.queryByRole("radio")).not.toBeInTheDocument();
  useCharSwapStore.getState().changeTrim([0, 30]);
  expect(useCharSwapStore.getState().trim).toEqual([0, 15]);
});

it("forces Kling for long selections and cannot switch that selection to MiniMax", async () => {
  useCharSwapStore.getState().setKling4Enabled(true);
  useCharSwapStore.getState().changeTrim([1, 31]);
  render(<GenerationModelChoice />);
  expect(screen.getByRole("radio", {name: /Kling/})).toBeChecked();
  expect(screen.getByRole("radio", {name: /MiniMax/})).toBeDisabled();
  useCharSwapStore.getState().setGenerationModel("minimax-h3");
  expect(useCharSwapStore.getState().generationModel).toBe("kling-4-0");
  expect(useCharSwapStore.getState().trim).toEqual([1, 31]);
  expect(getStepBlocker(useCharSwapStore.getState(), "trim")).toBeNull();
});

it("allows either model for short clips and locks Kling mesh off with masking on", async () => {
  const user = userEvent.setup();
  useCharSwapStore.getState().setKling4Enabled(true);
  useCharSwapStore.getState().setFaceMesh(true);
  render(<><GenerationModelChoice /><GenerationOptions isAdmin /></>);
  await user.click(screen.getByRole("radio", {name: /Kling/}));
  expect(screen.getByRole("switch", {name: "Face mesh"})).not.toBeChecked();
  expect(screen.getByRole("switch", {name: "Face mesh"})).toBeDisabled();
  expect(screen.getByRole("switch", {name: "Enhanced person masking"})).toBeChecked();
  expect(screen.getByRole("switch", {name: "Enhanced person masking"})).toBeDisabled();
  useCharSwapStore.getState().setFaceMesh(true);
  useCharSwapStore.getState().setEnhancedCharacterMasking(false);
  expect(useCharSwapStore.getState().faceMesh).toBe(false);
  expect(useCharSwapStore.getState().enhancedCharacterMasking).toBe(true);
  await user.click(screen.getByRole("radio", {name: /MiniMax/}));
  expect(screen.getByRole("switch", {name: "Face mesh"})).toBeEnabled();
});

it("offers MiniMax 2K independently of Kling access and freezes Kling quality at 720p",async()=>{
 const user=userEvent.setup();render(<GenerationModelChoice/>);
 await user.click(screen.getByRole("combobox",{name:"MiniMax quality"}));await user.click(screen.getByRole("option",{name:"2K · Higher detail"}));
 expect(useCharSwapStore.getState().generationResolution).toBe("2K");
 useCharSwapStore.getState().setKling4Enabled(true);useCharSwapStore.getState().setGenerationModel("kling-4-0");
 expect(useCharSwapStore.getState().generationResolution).toBe("720p");useCharSwapStore.getState().setGenerationResolution("2K");expect(useCharSwapStore.getState().generationResolution).toBe("720p");
});

it("hides 2K for users when access is off and blocks a stale 2K draft until 768p is chosen",async()=>{
 const user=userEvent.setup();useCharSwapStore.getState().setMinimax2kEnabled(false);render(<GenerationModelChoice/>);
 await user.click(screen.getByRole("combobox",{name:"MiniMax quality"}));expect(screen.queryByRole("option",{name:/2K/})).not.toBeInTheDocument();await user.click(screen.getByRole("option",{name:"768p · Standard"}));
 useCharSwapStore.setState({generationResolution:"2K"});expect(getStepBlocker(useCharSwapStore.getState(),"trim")).toMatch(/2K is currently unavailable/);
 await user.click(screen.getByRole("combobox",{name:"MiniMax quality"}));await user.click(screen.getByRole("option",{name:"768p · Standard"}));expect(useCharSwapStore.getState().generationResolution).toBe("768P");useCharSwapStore.getState().setMinimax2kEnabled(true);
});
