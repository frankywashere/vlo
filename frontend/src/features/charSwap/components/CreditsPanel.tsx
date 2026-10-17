import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  ButtonBase,
  Chip,
  CircularProgress,
  Slider,
  Stack,
  Typography,
} from "@mui/material";
import { startCreditCheckout } from "../services/commerce";
import { useLiveCredits } from "../hooks/useLiveCredits";
import { creditsForDuration } from "../utils/creditPricing";

interface CreditsPanelProps {
  credits?: ReturnType<typeof useLiveCredits>;
}

export function CreditsPanel({ credits }: CreditsPanelProps) {
  const ownCredits = useLiveCredits(!credits);
  const creditAccount = credits ?? ownCredits;
  const { catalog, wallet } = creditAccount;
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [seconds, setSeconds] = useState(10);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [checkoutUrl, setCheckoutUrl] = useState("");
  const pending = useRef(false);
  const mounted = useRef(true);
  const packs = catalog?.packs.filter((pack) => pack.active !== false) ?? [];
  const selected = packs.find((pack) => pack.id === selectedId) ?? packs[0];
  const rate =
    catalog?.credits_per_second ??
    (catalog?.credits_per_generation ?? 150) / 15;
  const minimum = creditsForDuration(4, catalog);
  const videoSeconds =
    wallet && rate > 0 ? Math.floor(wallet.balance / rate) : 0;
  const hasMinimum = wallet && wallet.balance >= minimum;
  useEffect(() => {
    mounted.current = true;
    let checks = 0;
    const timer = globalThis.setInterval(() => {
      globalThis.dispatchEvent(new Event("recastfx:credits-updated"));
      if (++checks >= 24) globalThis.clearInterval(timer);
    }, 5000);
    return () => {
      mounted.current = false;
      globalThis.clearInterval(timer);
    };
  }, []);
  async function checkout() {
    if (!selected || pending.current) return;
    pending.current = true;
    setBusy(true);
    setError("");
    setCheckoutUrl("");
    try {
      const result = await startCreditCheckout(selected.id);
      const url = new URL(result.url);
      if (url.protocol !== "https:" || url.hostname !== "checkout.stripe.com")
        throw new Error("Invalid checkout URL");
      if (mounted.current) setCheckoutUrl(result.url);
    } catch {
      if (mounted.current)
        setError("Checkout could not be opened. Please try again shortly.");
    } finally {
      pending.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  return (
    <Stack spacing={4}>
      {(error || creditAccount.error) && (
        <Alert severity="warning">{error || creditAccount.error}</Alert>
      )}
      <Box
        sx={{
          p: { xs: 2.5, sm: 3.5 },
          border: "1px solid",
          borderColor: "divider",
          borderRadius: "5px",
          bgcolor: "background.paper",
          display: "grid",
          gridTemplateColumns: { xs: "1fr", sm: "1fr auto" },
          gap: 3,
          alignItems: "center",
        }}
      >
        <Box>
          <Typography variant="body2" color="text.secondary">
            Credits available
          </Typography>
          <Stack
            direction="row"
            spacing={1}
            alignItems="baseline"
            sx={{ mt: 0.5 }}
          >
            <Typography
              sx={{
                fontSize: { xs: 44, sm: 52 },
                lineHeight: 1.15,
                fontWeight: 750,
                letterSpacing: "-.045em",
              }}
            >
              {wallet ? wallet.balance.toLocaleString() : "—"}
            </Typography>
            <Typography color="text.secondary">credits</Typography>
          </Stack>
          <Typography variant="body2" color="text.secondary" sx={{ mt: 1 }}>
            {!wallet
              ? "Checking your balance…"
              : hasMinimum
                ? `Enough for up to ${videoSeconds.toLocaleString()} seconds of video.`
                : `Add ${Math.max(0, minimum - wallet.balance).toLocaleString()} credits to make a 4-second video.`}
          </Typography>
        </Box>
        <Stack spacing={1} alignItems={{ xs: "flex-start", sm: "flex-end" }}>
          <Chip
            label={
              creditAccount.localPreview
                ? "Local test credits"
                : wallet
                  ? hasMinimum
                    ? "Ready to create"
                    : "Top up to create"
                  : "Loading balance"
            }
            size="small"
            color={hasMinimum ? "success" : "default"}
            variant="outlined"
          />
          <Button
            size="small"
            onClick={() =>
              globalThis.dispatchEvent(new Event("recastfx:credits-updated"))
            }
            sx={{ color: "text.secondary", px: 0 }}
          >
            Refresh balance
          </Button>
        </Stack>
      </Box>
      <Box>
        <Typography component="h2" variant="h5" fontWeight={700}>
          Buy credits
        </Typography>
        <Typography
          color="text.secondary"
          variant="body2"
          sx={{ mt: 0.5, mb: 2.5 }}
        >
          Pick a pack. Pay once, then create at your own pace.
        </Typography>
        {catalog?.configured && catalog.checkout_enabled && packs.length ? (
          <>
            <Box
              sx={{
                display: "grid",
                gridTemplateColumns: {
                  xs: "1fr",
                  sm: `repeat(${Math.min(3, packs.length)}, minmax(0, 1fr))`,
                },
                gap: 2,
              }}
            >
              {packs.map((pack) => (
                <ButtonBase
                  key={pack.id}
                  aria-label={`${pack.name}: ${pack.credits.toLocaleString()} credits · US$${(pack.price_cents / 100).toFixed(2)}`}
                  aria-pressed={selected?.id === pack.id}
                  disabled={busy}
                  onClick={() => {
                    setSelectedId(pack.id);
                    setCheckoutUrl("");
                    setError("");
                  }}
                  sx={{
                    display: "block",
                    p: { xs: 2.5, sm: 3 },
                    textAlign: "left",
                    border: "1px solid",
                    borderColor:
                      selected?.id === pack.id ? "primary.main" : "divider",
                    borderRadius: "5px",
                    bgcolor:
                      selected?.id === pack.id
                        ? "action.selected"
                        : "background.default",
                    boxShadow:
                      selected?.id === pack.id
                        ? "inset 0 0 0 1px currentColor"
                        : "none",
                    color:
                      selected?.id === pack.id
                        ? "primary.main"
                        : "text.primary",
                    transition: "background-color .15s, border-color .15s",
                    "&:hover": { borderColor: "primary.main" },
                    "&:focus-visible": {
                      outline: "2px solid",
                      outlineColor: "primary.main",
                      outlineOffset: 3,
                    },
                  }}
                >
                  <Stack
                    direction="row"
                    alignItems="center"
                    justifyContent="space-between"
                    gap={1}
                    sx={{ mb: 2 }}
                  >
                    <Typography variant="body2" fontWeight={700}>
                      {pack.name}
                    </Typography>
                    <Typography
                      variant="caption"
                      aria-hidden={selected?.id !== pack.id}
                      sx={{ opacity: selected?.id === pack.id ? 1 : 0 }}
                    >
                      Selected
                    </Typography>
                  </Stack>
                  <Typography
                    sx={{
                      fontSize: 36,
                      fontWeight: 750,
                      letterSpacing: "-.04em",
                      lineHeight: 1.2,
                      color: "text.primary",
                    }}
                  >
                    {pack.credits.toLocaleString()}
                  </Typography>
                  <Typography variant="body2" color="text.secondary">
                    credits
                  </Typography>
                  <Box
                    sx={{
                      borderTop: "1px solid",
                      borderColor:
                        selected?.id === pack.id ? "primary.light" : "divider",
                      pt: 2,
                      mt: 2.5,
                    }}
                  >
                    <Typography
                      sx={{
                        fontSize: 26,
                        fontWeight: 700,
                        color: "text.primary",
                        letterSpacing: "-.025em",
                      }}
                    >
                      US${(pack.price_cents / 100).toFixed(2)}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      One-time payment
                    </Typography>
                  </Box>
                  <Typography
                    variant="body2"
                    color="text.secondary"
                    sx={{ mt: 2 }}
                  >
                    About{" "}
                    {Math.floor(
                      pack.credits / creditsForDuration(15, catalog),
                    ).toLocaleString()}{" "}
                    × 15-second videos
                  </Typography>
                </ButtonBase>
              ))}
            </Box>
            <Stack
              direction={{ xs: "column", sm: "row" }}
              justifyContent="space-between"
              alignItems={{ sm: "center" }}
              gap={2}
              sx={{ mt: 2.5 }}
            >
              <Typography variant="caption" color="text.secondary">
                Secure payment with Stripe. Review your total before paying.
              </Typography>
              <Button
                variant="contained"
                size="large"
                disabled={busy || !selected}
                {...(creditAccount.localPreview
                  ? {
                      component: "a" as const,
                      href: "https://recastfx.com/?page=settings&tab=credits",
                      target: "_blank",
                      rel: "noopener noreferrer",
                    }
                  : { onClick: () => void checkout() })}
                sx={{ flexShrink: 0 }}
              >
                {creditAccount.localPreview ? (
                  "Buy credits on recastfx.com"
                ) : busy ? (
                  <>
                    <CircularProgress
                      size={16}
                      color="inherit"
                      sx={{ mr: 1 }}
                    />
                    Opening checkout…
                  </>
                ) : (
                  `Continue with ${selected?.name ?? "credits"} · US$${((selected?.price_cents ?? 0) / 100).toFixed(2)}`
                )}
              </Button>
            </Stack>
          </>
        ) : (
          <Box
            sx={{
              p: 3,
              border: "1px dashed",
              borderColor: "divider",
              borderRadius: "5px",
            }}
          >
            <Typography color="text.secondary">
              {catalog
                ? "Checkout is not available yet."
                : "Loading credit packs…"}
            </Typography>
          </Box>
        )}
      </Box>
      {catalog?.stripe_mode === "test" && (
        <Alert severity="info">
          Sandbox checkout: no real payment is taken.
        </Alert>
      )}
      {checkoutUrl && (
        <Alert
          severity="success"
          action={
            <Button
              component="a"
              href={checkoutUrl}
              target="_blank"
              rel="noopener noreferrer"
            >
              Continue to Stripe
            </Button>
          }
        >
          Your secure checkout is ready. Your clip stays saved in this tab.
        </Alert>
      )}
      {catalog?.configured && (
        <Box
          sx={{
            p: { xs: 2.5, sm: 3 },
            border: "1px solid",
            borderColor: "divider",
            borderRadius: "5px",
            display: "grid",
            gridTemplateColumns: { xs: "1fr", sm: "1fr 1fr" },
            gap: { xs: 2, sm: 5 },
          }}
        >
          <Box>
            <Typography component="h2" variant="h6">
              What does a video cost?
            </Typography>
            <Typography
              variant="body2"
              color="text.secondary"
              sx={{ mt: 0.75 }}
            >
              {rate} credits per second. A 15-second clip costs{" "}
              {catalog.credits_per_generation} credits.
            </Typography>
            <Typography
              variant="caption"
              color="text.secondary"
              sx={{ display: "block", mt: 1 }}
            >
              You’ll see the final credit cost before generating.
            </Typography>
          </Box>
          <Box>
            <Stack
              direction="row"
              justifyContent="space-between"
              alignItems="baseline"
            >
              <Typography variant="body2">{seconds}-second video</Typography>
              <Typography
                fontWeight={750}
                color="primary.main"
                aria-live="polite"
              >
                {creditsForDuration(seconds, catalog)} credits
              </Typography>
            </Stack>
            <Slider
              aria-label="Clip length estimate"
              min={4}
              max={15}
              step={1}
              value={seconds}
              onChange={(_, value) => setSeconds(Number(value))}
              sx={{ mt: 1 }}
            />
            <Stack direction="row" justifyContent="space-between">
              <Typography variant="caption" color="text.secondary">
                4 seconds
              </Typography>
              <Typography variant="caption" color="text.secondary">
                15 seconds
              </Typography>
            </Stack>
          </Box>
        </Box>
      )}
      {!!wallet?.transactions.length && (
        <Box>
          <Typography component="h2" variant="h6" sx={{ mb: 1 }}>
            Recent activity
          </Typography>
          {wallet.transactions
            .slice(-8)
            .reverse()
            .map((transaction) => (
              <Stack
                key={transaction.reference}
                direction="row"
                justifyContent="space-between"
                alignItems="center"
                gap={2}
                sx={{
                  py: 1.75,
                  borderBottom: "1px solid",
                  borderColor: "divider",
                }}
              >
                <Box>
                  <Typography
                    variant="body2"
                    sx={{ textTransform: "capitalize" }}
                  >
                    {transaction.kind.replaceAll("_", " ")}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">
                    {new Date(transaction.at * 1000).toLocaleDateString()}
                  </Typography>
                </Box>
                <Typography
                  variant="body2"
                  fontWeight={700}
                  color={
                    transaction.amount > 0 ? "success.main" : "text.primary"
                  }
                >
                  {transaction.amount > 0 ? "+" : ""}
                  {transaction.amount.toLocaleString()} credits
                </Typography>
              </Stack>
            ))}
        </Box>
      )}
    </Stack>
  );
}
