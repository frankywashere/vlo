import { Box, ButtonBase, Typography } from "@mui/material";
import CheckRounded from "@mui/icons-material/CheckRounded";
import type { Step } from "../model/model";

const VISIBLE_STEPS = [
  ["clip", "Clip"],
  ["people", "Person"],
  ["references", "Character"],
  ["review", "Generate"],
] as const;

interface CreationProgressProps {
  step: Step;
  manualReview?: boolean;
  sceneReviewed?: boolean;
  canVisit: (step: Step) => boolean;
  onVisit: (step: Step) => void;
}

export function CreationProgress({
  step,
  manualReview = false,
  sceneReviewed = false,
  canVisit,
  onVisit,
}: CreationProgressProps) {
  const steps: ReadonlyArray<readonly [Step, string]> = manualReview
    ? [["clip", "Clip"], ["people", "People"], ["references", "Character"], ["matches", "Check scenes"], ["review", "Generate"]]
    : VISIBLE_STEPS;
  const visibleStep =
    step === "trim"
      ? "clip"
      : manualReview && step === "review" && !sceneReviewed ? "matches"
      : step === "generation" || !manualReview && step === "matches"
        ? "review"
        : step;
  const current = steps.findIndex(([id]) => id === visibleStep);
  return (
    <Box
      component="nav"
      aria-label="Replacement steps"
      sx={{
        position: "relative",
        display: "grid",
        gridTemplateColumns: `repeat(${steps.length}, minmax(0, 1fr))`,
        mb: { xs: 2, md: 2.5 },
        pt: 1,
      }}
    >
      <Box
        aria-hidden
        sx={{
          position: "absolute",
          top: 22,
          left: `${50 / steps.length}%`,
          right: `${50 / steps.length}%`,
          height: 2,
          bgcolor: "divider",
        }}
      >
        <Box
          sx={{
            height: "100%",
            width: (Math.max(0, current) / (steps.length - 1)) * 100 + "%",
            bgcolor: "primary.main",
            transition: "width 250ms ease",
          }}
        />
      </Box>
      {steps.map(([id, label], index) => (
        <ButtonBase
          key={id}
          aria-label={label}
          aria-current={index === current ? "step" : undefined}
          disabled={
            !canVisit(
              id === "review" && step === "generation" ? "generation" : id,
            )
          }
          onClick={() =>
            onVisit(
              id === "review" && step === "generation" ? "generation" : id,
            )
          }
          sx={{
            position: "relative",
            display: "flex",
            flexDirection: "column",
            gap: 1,
            minHeight: 60,
            minWidth: 0,
            px: 0.25,
            borderRadius: "5px",
            color: index <= current ? "text.primary" : "text.secondary",
            "&.Mui-disabled": {
              color: index <= current ? "text.primary" : "text.disabled",
            },
          }}
        >
          <Box
            component="span"
            aria-hidden
            sx={{
              width: 28,
              height: 28,
              display: "grid",
              placeItems: "center",
              borderRadius: "50%",
              border: "2px solid",
              borderColor: index <= current ? "primary.main" : "divider",
              bgcolor: index <= current ? "primary.main" : "background.default",
              color:
                index <= current ? "primary.contrastText" : "text.secondary",
              boxShadow: (theme) =>
                "0 0 0 5px " + theme.palette.background.default,
              fontSize: 12,
              fontWeight: 700,
            }}
          >
            {index < current ? (
              <CheckRounded sx={{ fontSize: 16 }} />
            ) : (
              index + 1
            )}
          </Box>
          <Typography
            component="span"
            sx={{
              fontSize: { xs: 10, sm: 12 },
              fontWeight: index === current ? 750 : 500,
            }}
          >
            {label}
          </Typography>
        </ButtonBase>
      ))}
    </Box>
  );
}
