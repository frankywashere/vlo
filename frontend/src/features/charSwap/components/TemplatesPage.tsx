import { useEffect, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Chip,
  Stack,
  Typography,
  useMediaQuery,
} from "@mui/material";
import { loadTemplates, SAMPLE_TEMPLATES } from "../services/templates";
import type { TemplateCategory } from "../services/templates";

interface TemplatesPageProps {
  onSelect: (id: string) => void;
  disabled?: boolean;
}
export function TemplatesPage({
  onSelect,
  disabled = false,
}: TemplatesPageProps) {
  const [templates, setTemplates] = useState(SAMPLE_TEMPLATES);
  const [categories, setCategories] = useState<TemplateCategory[]>(
    [...new Set(SAMPLE_TEMPLATES.map((item) => item.category))].map((name) => ({
      id: name.toLowerCase(),
      name,
    })),
  );
  const [category, setCategory] = useState("all");
  const [page, setPage] = useState(0);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  const wide = useMediaQuery("(min-width:1200px)");
  const medium = useMediaQuery("(min-width:600px)");
  useEffect(() => {
    let active = true;
    let controller: AbortController | undefined;
    function refresh() {
      controller?.abort();
      controller = new AbortController();
      void loadTemplates(controller.signal)
        .then((result) => {
          if (!active) return;
          setTemplates(result.templates);
          setCategories(result.categories);
          setError("");
        })
        .catch((cause) => {
          if (active && cause?.name !== "AbortError")
            setError(
              "Saved templates could not load. You can still use the sample clips.",
            );
        });
    }
    refresh();
    const timer = globalThis.setInterval(refresh, 10 * 60 * 1000);
    globalThis.addEventListener("focus", refresh);
    return () => {
      active = false;
      controller?.abort();
      globalThis.clearInterval(timer);
      globalThis.removeEventListener("focus", refresh);
    };
  }, [revision]);
  const pageSize = (wide ? 5 : medium ? 3 : 2) * 2;
  const selectedCategory = categories.some((item) => item.id === category)
    ? category
    : "all";
  const filtered = templates.filter(
    (item) =>
      selectedCategory === "all" || item.category_id === selectedCategory,
  );
  const current = Math.min(
    page,
    Math.max(0, Math.ceil(filtered.length / pageSize) - 1),
  );
  return (
    <Box
      component="main"
      sx={{
        maxWidth: 1200,
        mx: "auto",
        px: { xs: 2, md: 4 },
        py: { xs: 4, md: 6 },
      }}
    >
      <Typography
        component="h1"
        variant="h3"
        fontWeight={750}
        sx={{ fontSize: { xs: 32, md: 48 }, letterSpacing: "-.04em" }}
      >
        Find your next scene.
      </Typography>
      <Typography color="text.secondary" sx={{ mt: 1, mb: 3 }}>
        Pick a scene. Make it yours.
      </Typography>
      {error && (
        <Alert
          severity="info"
          sx={{ mb: 2 }}
          action={
            <Button
              size="small"
              onClick={() => setRevision((value) => value + 1)}
            >
              Retry
            </Button>
          }
        >
          {error}
        </Alert>
      )}
      <Stack
        direction="row"
        gap={1}
        aria-label="Template categories"
        sx={{
          overflowX: "auto",
          pb: 2.5,
          scrollbarWidth: "none",
          "&::-webkit-scrollbar": { display: "none" },
        }}
      >
        {[{ id: "all", name: "All" }, ...categories].map((item) => (
          <Chip
            key={item.id}
            label={item.name}
            onClick={() => {
              setCategory(item.id);
              setPage(0);
            }}
            color={item.id === selectedCategory ? "primary" : "default"}
            variant={item.id === selectedCategory ? "filled" : "outlined"}
          />
        ))}
      </Stack>
      <Box
        sx={{
          display: "grid",
          gridTemplateColumns: {
            xs: "repeat(2,minmax(0,1fr))",
            sm: "repeat(3,minmax(0,1fr))",
            lg: "repeat(5,minmax(0,1fr))",
          },
          gap: { xs: 1.5, sm: 2 },
        }}
      >
        {filtered
          .slice(current * pageSize, (current + 1) * pageSize)
          .map((item) => (
            <Button
              key={item.id}
              disabled={disabled}
              aria-label={"Use template: " + item.title}
              onClick={() => onSelect(item.id)}
              sx={{
                p: 0,
                display: "block",
                textAlign: "left",
                color: "text.primary",
                overflow: "hidden",
                border: "1px solid",
                borderColor: "divider",
                borderRadius: "6px",
                "@media (hover: hover)": {
                  "&:hover": {
                    transform: "translateY(-3px)",
                    borderColor: "primary.main",
                    boxShadow: "0 8px 24px #0000000d",
                  },
                },
                "&:focus-visible": {
                  outline: "2px solid",
                  outlineColor: "primary.main",
                  outlineOffset: 3,
                },
              }}
            >
              <Box
                component="img"
                src={item.poster}
                alt=""
                loading="lazy"
                sx={{
                  width: "100%",
                  aspectRatio: { xs: "4/3", sm: "3/4" },
                  objectFit: "cover",
                  objectPosition: item.position ?? "center",
                  display: "block",
                  transition: "transform 180ms ease",
                }}
              />
              <Box sx={{ p: { xs: 1.5, sm: 1.75 } }}>
                <Typography variant="body2" fontWeight={650}>
                  {item.title}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {item.category}
                  {item.anchor ? " · Person ready" : ""}
                </Typography>
              </Box>
            </Button>
          ))}
      </Box>
      {!filtered.length && (
        <Typography color="text.secondary" sx={{ py: 6 }}>
          No templates in this category yet.
        </Typography>
      )}
      {filtered.length > pageSize && (
        <Stack
          direction="row"
          justifyContent="space-between"
          alignItems="center"
          sx={{ mt: 2.5 }}
        >
          <Button disabled={current === 0} onClick={() => setPage(current - 1)}>
            Previous
          </Button>
          <Typography variant="caption" color="text.secondary">
            {current + 1} / {Math.ceil(filtered.length / pageSize)}
          </Typography>
          <Button
            disabled={(current + 1) * pageSize >= filtered.length}
            onClick={() => setPage(current + 1)}
          >
            Next
          </Button>
        </Stack>
      )}
    </Box>
  );
}
