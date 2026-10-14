import { act, cleanup, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { readLocalClip, validateReference } from "../../services/localMedia";
import { useCharSwapStore } from "../../useCharSwapStore";
import type { ClipAsset, TargetPerson } from "../../model/model";
import { useLocalAssets } from "../useLocalAssets";

vi.mock("../../services/localMedia", () => ({
  readLocalClip: vi.fn(),
  validateReference: vi.fn(),
}));

const BaseURL = globalThis.URL;
const readClip = vi.mocked(readLocalClip);
const validateImage = vi.mocked(validateReference);
const createUrl = vi.fn<(asset: Blob | MediaSource) => string>();
const revokeUrl = vi.fn<(url: string) => void>();

const CLIP: ClipAsset = {
  id: "clip",
  name: "Source.mp4",
  url: "/source.mp4",
  duration: 5,
  width: 854,
  height: 480,
  mode: "demo",
};
const PERSON: TargetPerson = {
  id: "person",
  name: "Person 1",
  color: "#75baff",
  anchor: { x: 0.5, y: 0.5, time: 0 },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => {
    resolve = resolvePromise;
    reject = rejectPromise;
  });
  return { promise, resolve, reject };
}

function referenceFile(name: string) {
  return new File(["image"], name, { type: "image/png" });
}

function preparePerson() {
  useCharSwapStore.getState().loadClip(CLIP);
  useCharSwapStore.getState().selectTarget(PERSON);
}

beforeEach(() => {
  useCharSwapStore.getState().reset();
  readClip.mockReset();
  validateImage.mockReset();
  createUrl.mockReset();
  revokeUrl.mockReset();
  let nextUrl = 0;
  createUrl.mockImplementation(() => `blob:asset-${++nextUrl}`);
  vi.stubGlobal(
    "URL",
    class extends BaseURL {
      static createObjectURL = createUrl;
      static revokeObjectURL = revokeUrl;
    },
  );
});

afterEach(() => {
  cleanup();
  useCharSwapStore.getState().reset();
  vi.unstubAllGlobals();
});

describe("local asset upload lifecycle", () => {
  it("does not reload a pending clip after cancelling and resetting the swap", async () => {
    const metadata = deferred<ClipAsset>();
    readClip.mockReturnValueOnce(metadata.promise);
    const { result } = renderHook(() => useLocalAssets());
    let upload!: Promise<void>;
    act(() => {
      upload = result.current.importClip(
        new File(["video"], "old.mp4", { type: "video/mp4" }),
      );
    });
    expect(result.current.busy).toBe(true);
    act(() => {
      result.current.cancelPending();
      useCharSwapStore.getState().reset();
    });
    await act(async () => {
      metadata.resolve({ ...CLIP, mode: "upload", url: "blob:asset-1" });
      await upload;
    });
    expect(useCharSwapStore.getState().clip).toBeNull();
    expect(result.current.busy).toBe(false);
    expect(revokeUrl).toHaveBeenCalledWith("blob:asset-1");
  });

  it("keeps a newer reference when an older upload finishes last", async () => {
    preparePerson();
    const older = deferred<void>();
    const newer = deferred<void>();
    validateImage
      .mockReturnValueOnce(older.promise)
      .mockReturnValueOnce(newer.promise);
    const { result } = renderHook(() => useLocalAssets());
    let firstUpload!: Promise<void>;
    let secondUpload!: Promise<void>;
    act(() => {
      firstUpload = result.current.importReference(
        PERSON.id,
        referenceFile("old.png"),
      );
      secondUpload = result.current.importReference(
        PERSON.id,
        referenceFile("new.png"),
      );
    });
    expect(result.current.pendingReferences).toEqual([PERSON.id]);
    await act(async () => {
      newer.resolve(undefined);
      await secondUpload;
    });
    expect(useCharSwapStore.getState().references[0]?.name).toBe("new.png");
    expect(result.current.pendingReferences).toEqual([]);
    await act(async () => {
      older.resolve(undefined);
      await firstUpload;
    });
    expect(useCharSwapStore.getState().references[0]?.url).toBe("blob:asset-2");
    expect(revokeUrl).toHaveBeenCalledWith("blob:asset-1");
    expect(revokeUrl).not.toHaveBeenCalledWith("blob:asset-2");
  });

  it("does not carry pending reference work into a reset session with the same sample person ID", async () => {
    preparePerson();
    const validation = deferred<void>();
    validateImage.mockReturnValueOnce(validation.promise);
    const { result } = renderHook(() => useLocalAssets());
    let upload!: Promise<void>;
    act(() => {
      upload = result.current.importReference(
        PERSON.id,
        referenceFile("old.png"),
      );
      result.current.cancelPending();
      useCharSwapStore.getState().reset();
      preparePerson();
    });
    await act(async () => {
      validation.resolve(undefined);
      await upload;
    });
    expect(useCharSwapStore.getState().assignments).toEqual({});
    expect(useCharSwapStore.getState().references).toEqual([]);
    expect(result.current.pendingReferences).toEqual([]);
    expect(revokeUrl).toHaveBeenCalledWith("blob:asset-1");
  });

  it("rejects pending images after removing and reselecting the same person", async () => {
    preparePerson();
    const validation = deferred<void>();
    validateImage.mockReturnValueOnce(validation.promise);
    const { result } = renderHook(() => useLocalAssets());
    let upload!: Promise<void>;
    act(() => {
      upload = result.current.importReference(
        PERSON.id,
        referenceFile("old.png"),
      );
      useCharSwapStore.getState().selectTarget(PERSON);
      useCharSwapStore.getState().selectTarget(PERSON);
    });
    await act(async () => {
      validation.resolve(undefined);
      await upload;
    });
    expect(useCharSwapStore.getState().assignments).toEqual({});
    expect(revokeUrl).toHaveBeenCalledWith("blob:asset-1");
  });

  it("releases replaced clip and reference URLs while retaining assets still in use", async () => {
    readClip.mockImplementation(async (file, url) => ({
      ...CLIP,
      id: file.name,
      name: file.name,
      url,
      mode: "upload",
    }));
    validateImage.mockResolvedValue(undefined);
    const { result, unmount } = renderHook(() => useLocalAssets());
    await act(async () => {
      await result.current.importClip(
        new File(["one"], "one.mp4", { type: "video/mp4" }),
      );
    });
    expect(revokeUrl).not.toHaveBeenCalledWith("blob:asset-1");
    await act(async () => {
      await result.current.importClip(
        new File(["two"], "two.mp4", { type: "video/mp4" }),
      );
    });
    expect(revokeUrl).toHaveBeenCalledWith("blob:asset-1");
    expect(revokeUrl).not.toHaveBeenCalledWith("blob:asset-2");
    act(() => useCharSwapStore.getState().selectTarget(PERSON));
    await act(async () => {
      await result.current.importReference(PERSON.id, referenceFile("one.png"));
    });
    expect(revokeUrl).not.toHaveBeenCalledWith("blob:asset-3");
    await act(async () => {
      await result.current.importReference(PERSON.id, referenceFile("two.png"));
    });
    expect(revokeUrl).toHaveBeenCalledWith("blob:asset-3");
    expect(revokeUrl).not.toHaveBeenCalledWith("blob:asset-4");
    unmount();
    expect(revokeUrl).toHaveBeenCalledWith("blob:asset-2");
    expect(revokeUrl).toHaveBeenCalledWith("blob:asset-4");
  });
});
