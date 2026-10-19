import { Avatar, Box, Stack, Typography } from "@mui/material";
import type { TargetPerson } from "../model/model";

interface PersonBadgeProps {
  person: TargetPerson;
  subtitle?: string;
  compact?: boolean;
  stackedOnMobile?: boolean;
}
export function PersonBadge({
  person,
  subtitle,
  compact,
  stackedOnMobile,
}: PersonBadgeProps) {
  return (
    <Stack
      direction={{ xs: stackedOnMobile ? "column" : "row", sm: "row" }}
      spacing={1.5}
      alignItems="center"
      sx={{ minWidth: 0 }}
    >
      <Avatar
        variant="rounded"
        src={person.thumbnail}
        alt={person.name}
        sx={{
          width: compact ? 38 : 56,
          height: compact ? 38 : 56,
          border: `2px solid ${person.color}`,
          bgcolor: `${person.color}22`,
          color: person.color,
          "& img": { objectPosition: "center top" },
        }}
      >
        {person.name.charAt(0)}
      </Avatar>
      <Box
        sx={{
          minWidth: 0,
          textAlign: { xs: stackedOnMobile ? "center" : "left", sm: "left" },
        }}
      >
        <Typography fontWeight={600} sx={{ overflowWrap: "anywhere" }}>
          {person.name}
        </Typography>
        {subtitle && (
          <Typography variant="body2" color="text.secondary">
            {subtitle}
          </Typography>
        )}
      </Box>
    </Stack>
  );
}
