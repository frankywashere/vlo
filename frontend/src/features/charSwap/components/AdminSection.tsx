import { useState } from "react";
import type { ReactNode } from "react";
import { Box, Card, CardContent, Typography } from "@mui/material";

interface AdminSectionProps {
  title: string;
  children: ReactNode;
  defaultExpanded?: boolean;
  onExpandedChange?: (expanded: boolean) => void;
}

/** Native disclosure keeps form values mounted when a section is collapsed. */
export function AdminSection({ title, children, defaultExpanded = false, onExpandedChange }: AdminSectionProps) {
  const [expanded, setExpanded] = useState(defaultExpanded);
  return <Card variant="outlined"><Box component="details" open={expanded}
    onToggle={(event) => { setExpanded(event.currentTarget.open); onExpandedChange?.(event.currentTarget.open); }}>
    <Box component="summary" sx={{cursor: "pointer", p: {xs: 2, md: 3}, minHeight: 48, overflowWrap: "anywhere"}}>
      <Typography component="span" role="heading" aria-level={2} variant="h5">{title}</Typography>
    </Box>
    <CardContent sx={{pt: 0}}>{children}</CardContent>
  </Box></Card>;
}
