import { expect, it } from "vitest";
import { localPreviewWallet } from "../localPreviewCredits";

it("limits the requested 100 test credits to the configured account on loopback", () => {
  const wallet = (email: string, host: string) =>
    localPreviewWallet(email, host, "4deleted@gmail.com", "100");
  expect(wallet("4deleted@gmail.com", "localhost")?.balance).toBe(100);
  expect(wallet("4deleted@gmail.com", "127.0.0.1")?.balance).toBe(100);
  expect(wallet("other@example.com", "localhost")).toBeNull();
  expect(wallet("4deleted@gmail.com", "recastfx.com")).toBeNull();
  expect(wallet("4deleted@gmail.com", "192.168.86.48")).toBeNull();
  expect(
    localPreviewWallet("4deleted@gmail.com", "localhost", undefined, "100"),
  ).toBeNull();
  expect(
    localPreviewWallet(
      "4deleted@gmail.com",
      "localhost",
      "4deleted@gmail.com",
      "NaN",
    ),
  ).toBeNull();
});
