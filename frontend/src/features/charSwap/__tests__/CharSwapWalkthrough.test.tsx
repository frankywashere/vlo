import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { CharSwapWalkthrough } from "../CharSwapWalkthrough";
import { useCharSwapStore } from "../useCharSwapStore";
import type { DemoManifest } from "../services/demo";
vi.mock("../hooks/useLiveCredits", () => ({
  useLiveCredits: () => ({
    catalog: null,
    wallet: { balance: 2000 },
    error: "",
  }),
}));
vi.mock("../services/commerce", () => ({
  loadAccountAccess: async () => ({ api_key_enabled: false, role: null }),
}));

const sample: DemoManifest = {
  clip: {
    id: "sample",
    name: "sample.mp4",
    url: "/demo.mp4",
    mode: "demo",
    duration: 5,
    width: 854,
    height: 480,
  },
  poster: "/poster.jpg",
  reference: { id: "teddy", name: "Teddy", url: "/teddy.png" },
  people: [1, 2].map((id) => ({
    id: `person-${id}`,
    name: `Person ${id}`,
    thumbnail: `/person-${id}.jpg`,
    color: "#75baff",
    boxes: [{ time: 0, x: 0.1 * id, y: 0.1, width: 0.2, height: 0.7 }],
  })),
};

beforeEach(() => {
  useCharSwapStore.getState().reset();
  useCharSwapStore.getState().setEasyMode(false);
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(JSON.stringify(sample), {
      headers: { "content-type": "application/json" },
    }),
  );
  vi.spyOn(window, "scrollTo").mockImplementation(() => undefined);
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(
    () => undefined,
  );
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("CharSwap walkthrough", () => {
  it("requires credits before a clip can be uploaded or a template opened", async () => {
    const user = userEvent.setup();
    const onCredits = vi.fn();
    render(
      <CharSwapWalkthrough
        credits={{
          catalog: null,
          wallet: { balance: 0, transactions: [] },
          error: "",
        }}
        onCredits={onCredits}
      />,
    );
    expect(
      screen.getByRole("heading", { name: "Add credits to start creating" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "Upload your clip" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByLabelText("Upload source clip"),
    ).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "View credit packs" }));
    expect(onCredits).toHaveBeenCalledOnce();
    expect(globalThis.fetch).not.toHaveBeenCalled();
  });
  it("takes two selected people through assignment and review without submitting a paid job", async () => {
    const user = userEvent.setup();
    useCharSwapStore.getState().loadClip(sample.clip);
    render(<CharSwapWalkthrough />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Select person" }),
      ).toBeEnabled(),
    );
    if (useCharSwapStore.getState().clip?.mode === "upload") {
      expect(screen.getByRole("button", { name: "Select person" })).toBeDisabled();
      await user.click(screen.getByRole("checkbox", {name: /I own this clip/}));
    }
    await user.click(screen.getByRole("button", { name: "Select person" }));
    await user.click(screen.getByRole("button", { name: "Select Person 1" }));
    await user.click(screen.getByRole("button", { name: "Select Person 2" }));
    await user.click(screen.getByRole("button", { name: "Choose characters" }));
    expect(screen.getByRole("button", { name: "Continue to generate" })).toBeDisabled();
    for (const button of screen.getAllByRole("button", {
      name: "Use sample teddy bear",
    }))
      await user.click(button);
    await user.click(screen.getByRole("button", { name: "Continue to generate" }));
    await user.click(screen.getByRole("button", { name: "Prepare and review" }));
    expect(screen.getByText(/Sample review moment/)).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Continue to generate" }),
    ).toBeDisabled();
    await user.click(
      screen.getByRole("button", { name: "Match Person 1 to Person 1" }),
    );
    await user.click(screen.getByRole("button", { name: "Section 2" }));
    // A matching UI must refuse two replacement identities using the same person.
    await user.click(
      screen.getByRole("button", { name: "Match Person 1 to Person 2" }),
    );
    expect(screen.getByRole("alert")).toHaveTextContent("already matched");
    expect(
      screen.getByRole("button", { name: "Continue to generate" }),
    ).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Not visible here" }));
    await user.click(
      screen.getByRole("button", { name: "Continue to generate" }),
    );
    expect(
      screen.getByRole("heading", { name: "Ready to generate?" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Generate video/ }),
    ).toBeDisabled();
    expect(
      screen.queryByText(
        /This local preview|Live generation is available on the secure app/,
      ),
    ).not.toBeInTheDocument();
    expect(globalThis.fetch).toHaveBeenCalledTimes(1);
    expect(globalThis.fetch).toHaveBeenCalledWith(
      "/charswap-demo/manifest.json",
    );
  });

  it("returns to character assignment from Generate before manual preparation has started", async () => {
    const user = userEvent.setup();
    const store = useCharSwapStore.getState();
    store.loadClip(sample.clip);
    store.selectTarget({...sample.people[0], anchor: {x: .2, y: .3, time: 0}});
    store.assignReference(sample.people[0].id, sample.reference);
    store.visit("review");
    render(<CharSwapWalkthrough />);
    await user.click(screen.getByRole("button", {name: "Back"}));
    expect(useCharSwapStore.getState().step).toBe("references");
    expect(useCharSwapStore.getState().references[0].id).toBe(sample.reference.id);
    expect(screen.queryByText(/Preparation has not started/)).not.toBeInTheDocument();
  });

  it("clears dependent choices when a reviewed clip is retrimmed", async () => {
    const user = userEvent.setup();
    useCharSwapStore.getState().loadClip(sample.clip);
    render(<CharSwapWalkthrough />);
    await waitFor(() =>
      expect(
        screen.getByRole("button", { name: "Select person" }),
      ).toBeEnabled(),
    );
    if (useCharSwapStore.getState().clip?.mode === "upload") {
      expect(screen.getByRole("button", { name: "Select person" })).toBeDisabled();
      await user.click(screen.getByRole("checkbox", {name: /I own this clip/}));
    }
    await user.click(screen.getByRole("button", { name: "Select person" }));
    await user.click(screen.getByRole("button", { name: "Select Person 1" }));
    const navigation = screen.getByRole("navigation", {
      name: "Replacement steps",
    });
    await user.click(within(navigation).getByRole("button", { name: "Clip" }));
    const end = screen.getByRole("spinbutton", { name: "End (seconds)" });
    await user.clear(end);
    await user.type(end, "4");
    await user.tab();
    expect(useCharSwapStore.getState().targets).toHaveLength(0);
    expect(
      within(navigation).getByRole("button", { name: "Character" }),
    ).toBeDisabled();
  });

  it("prices the selected trim inside Clip and proceeds directly to Person without an early top-up warning", async () => {
    const user = userEvent.setup();
    useCharSwapStore
      .getState()
      .loadClip({ ...sample.clip, mode: "upload", duration: 30 });
    render(
      <CharSwapWalkthrough
        credits={{
          catalog: {
            currency: "usd",
            packs: [],
            credits_per_generation: 195,
            credits_per_second: 13,
            usd_cents_per_credit: 1,
            minimum_pack_cents: 2000,
            stripe_mode: "test",
            configured: true,
            checkout_enabled: false,
          },
          wallet: { balance: 100, transactions: [] },
          error: "",
        }}
      />,
    );
    const navigation = screen.getByRole("navigation", {
      name: "Replacement steps",
    });
    expect(
      within(navigation)
        .getAllByRole("button")
        .map((button) => button.getAttribute("aria-label")),
    ).toEqual(["Clip", "People", "Character", "Check scenes", "Generate"]);
    expect(screen.queryByText(/This clip needs/)).not.toBeInTheDocument();
    expect(
      screen.getByRole("status", { name: "Credit estimate" }),
    ).toHaveTextContent("195 credits");
    expect(
      screen.getByRole("button", { name: "Select person" }),
    ).toBeDisabled();
    const end = screen.getByRole("spinbutton", { name: "End (seconds)" });
    fireEvent.change(end, { target: { value: "5" } });
    fireEvent.blur(end);
    expect(
      screen.getByRole("status", { name: "Credit estimate" }),
    ).toHaveTextContent("65 credits");
    if (useCharSwapStore.getState().clip?.mode === "upload") {
      expect(screen.getByRole("button", { name: "Select person" })).toBeDisabled();
      await user.click(screen.getByRole("checkbox", {name: /I own this clip/}));
    }
    await user.click(screen.getByRole("button", { name: "Select person" }));
    expect(useCharSwapStore.getState().step).toBe("people");
    expect(
      within(navigation).getByRole("button", { name: "People" }),
    ).toHaveAttribute("aria-current", "step");
    await user.click(screen.getByRole("button", { name: "Back" }));
    expect(useCharSwapStore.getState().step).toBe("clip");
    expect(
      screen.getByRole("spinbutton", { name: "End (seconds)" }),
    ).toHaveValue(5);
    expect(
      screen.queryByRole("button", { name: "Back" }),
    ).not.toBeInTheDocument();
  });
});

it("opens a waiting screen, permits Back without canceling, and advances when selected cleanup is done",async()=>{
  const store=useCharSwapStore.getState();store.loadClip(sample.clip);
  store.selectTarget({id:"a",name:"Person A",color:"#75baff",anchor:{x:.5,y:.5,time:0}});
  store.assignReference("a",{...sample.reference,cleanup:{originalUrl:sample.reference.url,mode:"clean",pending:true}});
  store.visit("references");render(<CharSwapWalkthrough/>);const user=userEvent.setup();
  const next=()=>screen.getByRole("button",{name:"Continue to generate"});
  expect(next()).toBeEnabled();await user.click(next());
  expect(screen.getByRole("heading",{name:"Cleaning up your images"})).toBeVisible();
  expect(screen.getByText("Character 1 of 1")).toBeVisible();
  await user.click(screen.getByRole("button",{name:"Back to characters"}));
  expect(useCharSwapStore.getState().references[0].cleanup?.pending).toBe(true);
  expect(next()).toBeEnabled();await user.click(next());
  act(()=>useCharSwapStore.getState().assignReference("a",{...sample.reference,cleanup:{originalUrl:sample.reference.url,mode:"clean",cleanedUrl:"blob:cleaned"}}));
  await waitFor(()=>expect(useCharSwapStore.getState().step).toBe("review"));
});
it("requires an explicit Original choice after a cleanup failure before advancing",async()=>{
 const s=useCharSwapStore.getState();s.loadClip(sample.clip);
 s.selectTarget({id:"a",name:"Person A",color:"#75baff",anchor:{x:.5,y:.5,time:0}});
 s.assignReference("a",{...sample.reference,cleanup:{originalUrl:sample.reference.url,mode:"original",error:"Try another image"}});s.visit("references");
 render(<CharSwapWalkthrough/>);const user=userEvent.setup();
 await user.click(screen.getByRole("button",{name:"Continue to generate"}));
 expect(useCharSwapStore.getState().step).toBe("references");
 await user.click(screen.getByRole("button",{name:"Use Original"}));
 await waitFor(()=>expect(useCharSwapStore.getState().step).toBe("review"));
});

it("allows a 15-second trim moved to a fractional start to advance",async()=>{
 const s=useCharSwapStore.getState();s.loadClip({...sample.clip,duration:20,mode:"upload"});
 s.changeTrim([1.1,16.1]);render(<CharSwapWalkthrough/>);
 const button=screen.getByRole("button",{name:"Select person"});
  await userEvent.setup().click(screen.getByRole("checkbox", {name: /I own this clip/}));
 expect(button).toBeEnabled();await userEvent.setup().click(button);
 expect(useCharSwapStore.getState().step).toBe("people");
});


it("requires permission after upload before button or progress navigation to people", async () => {
  const user = userEvent.setup();
  useCharSwapStore.getState().loadClip({...sample.clip, mode: "upload"});
  render(<CharSwapWalkthrough />);
  const next = screen.getByRole("button", {name: "Select person"});
  const progress = screen.getByRole("navigation", {name: "Replacement steps"});
  expect(next).toBeDisabled();
  expect(within(progress).getByRole("button", {name: "People"})).toBeDisabled();
  await user.click(screen.getByRole("checkbox", {name: /I own this clip/}));
  expect(next).toBeEnabled();
  await user.click(next);
  expect(useCharSwapStore.getState().step).toBe("people");
  await user.click(screen.getByRole("button", {name: "Back"}));
  expect(screen.getByRole("checkbox", {name: /I own this clip/})).toBeChecked();
  await user.click(screen.getByRole("button", {name: "Change clip"}));
  expect(screen.getByRole("button", {name: "Upload your clip"})).toBeEnabled();
  expect(screen.getByRole("button", {name: "Select person"})).toBeDisabled();
});
