import { Avatar, Box, Typography } from "@mui/material";
import type { AccountJob } from "../services/accounts";

interface CharacterStackProps {
  job: AccountJob;
}

export function CharacterStack({ job }: CharacterStackProps) {
  const references = job.reference_urls?.length
    ? job.reference_urls
    : job.reference_url
      ? [job.reference_url]
      : [];
  const images = [
    ...new Set(
      references.length
        ? references
        : (job.selection_targets ?? [])
            .map((target) => target.thumbnail_url)
            .filter((url): url is string => Boolean(url)),
    ),
  ];
  if (!images.length) return null;
  const visible = images.slice(0, 5);
  return (
    <Box
      role="group"
      aria-label={`${images.length} replacement ${images.length === 1 ? "character" : "characters"}`}
      sx={{
        position: "absolute",
        right: 14,
        bottom: 14,
        width: 36 + (visible.length - 1) * 24,
        height: 36,
        pointerEvents: "none",
      }}
    >
      {visible.map((url, index) => (
        <Avatar
          key={url}
          variant="circular"
          src={url}
          alt={`Replacement character ${index + 1}`}
          sx={{
            position: "absolute",
            left: index * 24,
            bottom: 0,
            width: 36,
            height: 36,
            border: "2px solid",
            borderColor: "background.paper",
            borderRadius: "50%",
            boxShadow: "0 3px 10px #0005",
            bgcolor: "background.paper",
            zIndex: index + 1,
            "& img": { objectFit: "cover", objectPosition: "50% 35%" },
          }}
        />
      ))}
      {images.length > 5 && (
        <Typography
          variant="caption"
          sx={{
            position: "absolute",
            right: -5,
            top: -8,
            zIndex: 6,
            bgcolor: "background.paper",
            color: "text.primary",
            px: 0.75,
            borderRadius: "5px",
            fontWeight: 700,
          }}
        >
          +{images.length - 5}
        </Typography>
      )}
    </Box>
  );
}
