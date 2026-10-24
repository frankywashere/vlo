import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Box, Button, Stack, TextField, Typography } from "@mui/material";
import { loadProcessingCapacity, saveProcessingCapacity } from "../services/commerce";
import type { ProcessingCapacity } from "../services/commerce";
import { AdminSection } from "./AdminSection";

interface ProcessingCapacityPanelProps {
  canEdit: boolean;
}

const MAX_CONFIGURED_JOBS = 256;

function validCeiling(value: number | undefined): value is number {
  return value !== undefined && Number.isInteger(value) && value >= 1 && value <= MAX_CONFIGURED_JOBS;
}

function validLimit(value: string, maximum: number) {
  return value.trim() !== "" && Number.isInteger(Number(value)) && Number(value) >= 1 && Number(value) <= maximum;
}

export function ProcessingCapacityPanel({ canEdit }: ProcessingCapacityPanelProps) {
  const [expanded, setExpanded] = useState(false);
  const [capacity, setCapacity] = useState<ProcessingCapacity | null>(null);
  const [heavyJobs, setHeavyJobs] = useState("");
  const [reapiJobs, setReapiJobs] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const request = useRef(0);

  const apply = useCallback((result: ProcessingCapacity) => {
    setCapacity(result);
    setHeavyJobs(String(result.heavy_jobs));
    setReapiJobs(String(result.reapi_jobs));
  }, []);

  const refresh = useCallback(async () => {
    const current = ++request.current;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await loadProcessingCapacity();
      if (current === request.current) apply(result);
    } catch (cause) {
      if (current === request.current) setError(cause instanceof Error ? cause.message : "Processing capacity is unavailable.");
    } finally {
      if (current === request.current) setBusy(false);
    }
  }, [apply]);

  useEffect(() => {
    if (expanded) void refresh();
    return () => { ++request.current; };
  }, [expanded, refresh]);

  const heavyCeiling = capacity?.infrastructure.heavy_ceiling;
  const reapiCeiling = capacity?.infrastructure.control_ceiling;
  const ceilingsValid = validCeiling(heavyCeiling) && validCeiling(reapiCeiling);
  const heavyMaximum = validCeiling(heavyCeiling) ? heavyCeiling : MAX_CONFIGURED_JOBS;
  const reapiMaximum = validCeiling(reapiCeiling) ? reapiCeiling : MAX_CONFIGURED_JOBS;
  const heavyValid = validLimit(heavyJobs, heavyMaximum);
  const reapiValid = validLimit(reapiJobs, reapiMaximum);
  const configurationReady = Boolean(capacity?.infrastructure.configured) && ceilingsValid;
  const editable = canEdit && configurationReady && !busy;
  const changed = capacity && (Number(heavyJobs) !== capacity.heavy_jobs || Number(reapiJobs) !== capacity.reapi_jobs);

  async function save() {
    if (!capacity || !editable || !changed || !heavyValid || !reapiValid) return;
    const current = ++request.current;
    setBusy(true); setError(""); setMessage("");
    try {
      const result = await saveProcessingCapacity({ revision: capacity.revision, heavy_jobs: Number(heavyJobs), reapi_jobs: Number(reapiJobs) });
      if (current === request.current) {
        apply(result);
        setMessage("Processing limits saved. Existing work continues; additional jobs wait when the limit is reached.");
      }
    } catch (cause) {
      if (current === request.current) setError(cause instanceof Error ? cause.message : "Processing limits could not be saved. Refresh before trying again.");
    } finally {
      if (current === request.current) setBusy(false);
    }
  }

  return <AdminSection title="Processing capacity" onExpandedChange={setExpanded}>
    <Stack spacing={2}>
      <Typography color="text.secondary">Additional jobs wait when a limit is reached. Lowering a limit does not cancel work already running.</Typography>
      {error && <Alert severity="error">{error}</Alert>}
      {message && <Alert severity="success">{message}</Alert>}
      {busy && !capacity && <Typography>Loading processing capacity…</Typography>}
      {capacity && <>
        {!configurationReady && <Alert severity="warning">Concurrency controls are unavailable until the worker configuration is ready.</Alert>}
        {!canEdit && <Typography variant="body2" color="text.secondary">Requires service-control permission to change limits.</Typography>}
        <Typography variant="body2" color="text.secondary">All API keys on the same reAPI account share its approved media-task limit. Keep the render limit within your account’s approved allowance.</Typography>
        <Typography variant="body2">Active processing jobs: {capacity.active.heavy} · Active reAPI renders: {capacity.active.reapi}</Typography>
        <Box component="form" onSubmit={event => { event.preventDefault(); void save(); }} sx={{ minWidth: 0 }}>
          <Stack spacing={2}>
            <TextField label="Simultaneous processing jobs" type="number" value={heavyJobs} disabled={!editable}
              onChange={event => { setHeavyJobs(event.target.value); setMessage(""); }}
              error={!heavyValid} helperText={heavyValid ? `Preparation and finishing share this limit. Choose 1–${heavyMaximum}.` : `Enter a whole number from 1 to ${heavyMaximum}.`}
              slotProps={{ htmlInput: { min: 1, max: heavyMaximum, step: 1 } }} />
            <TextField label="Simultaneous reAPI renders" type="number" value={reapiJobs} disabled={!editable}
              onChange={event => { setReapiJobs(event.target.value); setMessage(""); }}
              error={!reapiValid} helperText={reapiValid ? `Limits submitted renders still running at reAPI. Choose 1–${reapiMaximum}.` : `Enter a whole number from 1 to ${reapiMaximum}.`}
              slotProps={{ htmlInput: { min: 1, max: reapiMaximum, step: 1 } }} />
            <Stack direction="row" useFlexGap sx={{ gap: 1, flexWrap: "wrap" }}>
              <Button type="submit" variant="contained" disabled={!editable || !changed || !heavyValid || !reapiValid}>Save processing limits</Button>
              <Button type="button" disabled={busy} onClick={() => void refresh()}>Refresh processing capacity</Button>
            </Stack>
          </Stack>
        </Box>
      </>}
      {!capacity && !busy && <Button disabled={busy} onClick={() => void refresh()}>Refresh processing capacity</Button>}
    </Stack>
  </AdminSection>;
}
