import {
  Button,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
} from "@mui/material";
import { CreditsPanel } from "./CreditsPanel";

interface CommerceDialogProps {
  open: boolean;
  onClose: () => void;
}

export function CommerceDialog({ open, onClose }: CommerceDialogProps) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      fullWidth
      maxWidth="sm"
      aria-labelledby="commerce-title"
    >
      <DialogTitle id="commerce-title">Your credits</DialogTitle>
      <DialogContent dividers>{open && <CreditsPanel />}</DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Done</Button>
      </DialogActions>
    </Dialog>
  );
}
