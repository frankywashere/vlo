import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { disableNotifications, enableNotifications } from "../usePwa";

vi.mock("../../services/accountContext", () => ({
  accountToken: async () => "account-token",
}));
const permission = vi.fn();
const unsubscribe = vi.fn();
const subscribe = vi.fn();
const requests = vi.fn();
const subscription = {
  endpoint: "https://fcm.googleapis.com/device",
  toJSON: () => ({
    endpoint: "https://fcm.googleapis.com/device",
    keys: { auth: "auth", p256dh: "point" },
  }),
  unsubscribe,
};

beforeEach(() => {
  permission.mockReset().mockResolvedValue("granted");
  unsubscribe.mockReset().mockResolvedValue(true);
  subscribe.mockReset().mockResolvedValue(subscription);
  requests.mockReset();
  const registration = {
    pushManager: {
      getSubscription: vi.fn().mockResolvedValue(subscription),
      subscribe,
    },
  };
  vi.stubGlobal("isSecureContext", true);
  vi.stubGlobal("Notification", { requestPermission: permission });
  vi.stubGlobal("PushManager", class {});
  vi.stubGlobal("navigator", {
    serviceWorker: {
      ready: Promise.resolve(registration),
      getRegistration: async () => registration,
    },
  });
  vi.stubGlobal("fetch", requests);
});
afterEach(() => vi.unstubAllGlobals());

it("does not contact a server when notification permission is denied", async () => {
  permission.mockResolvedValue("denied");
  await expect(enableNotifications()).rejects.toThrow(
    "Notifications are blocked",
  );
  expect(requests).not.toHaveBeenCalled();
  expect(subscribe).not.toHaveBeenCalled();
});

it("refuses insecure contexts without prompting for permission", async () => {
  vi.stubGlobal("isSecureContext", false);
  await expect(enableNotifications()).rejects.toThrow("Use HTTPS");
  expect(permission).not.toHaveBeenCalled();
});

it("saves the device only to the authenticated account endpoint", async () => {
  requests
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ configured: true, public_key: "BA" })),
    )
    .mockResolvedValueOnce(new Response(JSON.stringify({ enabled: true })));
  await enableNotifications();
  expect(requests.mock.calls[1]?.[0]).toBe("/api/account/notifications");
  expect(requests.mock.calls[1]?.[1]).toEqual(
    expect.objectContaining({
      method: "POST",
      cache: "no-store",
      credentials: "omit",
      headers: {
        "X-Firebase-Token": "account-token",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ subscription: subscription.toJSON() }),
    }),
  );
});

it("revokes the browser subscription if saving to the account fails", async () => {
  requests
    .mockResolvedValueOnce(
      new Response(JSON.stringify({ configured: true, public_key: "BA" })),
    )
    .mockResolvedValueOnce(new Response("unavailable", { status: 503 }));
  await expect(enableNotifications()).rejects.toThrow(
    "Notifications are not available",
  );
  expect(unsubscribe).toHaveBeenCalledOnce();
});

it("disables this device even when the server cannot be reached", async () => {
  requests.mockRejectedValue(new Error("offline"));
  await expect(disableNotifications()).rejects.toThrow("offline");
  expect(unsubscribe).toHaveBeenCalledOnce();
});
