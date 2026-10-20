import { Box, Button, Stack, Typography } from "@mui/material";

export type LegalDocument = "privacy" | "terms";
const PRIVACY = [
  [
    "Who we are",
    "RecastFX provides video character replacement and a private generation library. Contact support@recastfx.com for support, privacy questions, access requests or account deletion.",
  ],
  [
    "Information we use",
    "Google sign-in provides your account identifier, email, display name and profile image. We process the clips, reference images, selected people, prompts and settings you submit, plus generated videos and job status. Template publishers can save face positions and a selected person; published template clips are available for others to reuse. We also keep credit purchases, balance changes, administrative actions and service diagnostics needed to operate the app, prevent abuse and resolve failed jobs.",
  ],
  [
    "Why we use it",
    "We use this information to sign you in, process your requested edits, show your library, manage payments and credits, protect accounts and troubleshoot the service. Upload only material you have permission to use. Clips and face images may contain personal information about other people; obtain their permission before uploading.",
  ],
  [
    "Service providers",
    "Google Firebase handles authentication. Google Cloud stores and processes application data. Generation and preparation may send your uploaded media, prompts and settings to the configured AI providers, including MiniMax through reAPI or ToAPIs, and Replicate where enabled. Stripe handles credit checkout; RecastFX does not receive your full payment-card details. These services may process data outside your country under their own terms and privacy policies. Provider data practices may differ; do not upload sensitive or confidential material without checking them.",
  ],
  [
    "Storage and retention",
    "Finished videos, branded before-and-after comparisons, the trimmed original clip and small character thumbnails are available in your private library for 30 days; download copies you want to keep. Full original uploads and intermediate processing files are temporary and may be removed sooner. Hiding a library item is not an account-deletion request. Account, billing, security and diagnostic records can be retained separately as needed to operate the service, address disputes and meet applicable obligations. Contact support for a deletion or access request; some financial or security records may need to be retained.",
  ],
  [
    "Your device and notifications",
    "Your browser stores your Google sign-in session and appearance preference. The installed app caches a public offline page, not your private library or API responses. Notifications are optional: enabling them shares a device push subscription with the server and browser push service. Ready notifications contain a generic library link, not your uploaded media. Turn them off in Account settings or your browser’s site settings.",
  ],
  [
    "Your choices",
    "You can update your display name, export library details, download videos, sign out, or contact support to request access, correction or deletion. If you do not want uploaded material processed by the service and its providers, do not submit it. We will update this page when our practices change and seek further consent where required.",
  ],
];
const TERMS = [
  [
    "Using RecastFX",
    "By using RecastFX or continuing with Google, you agree to these Terms and acknowledge the Privacy Policy. Use the service only if you can form a binding agreement under the laws that apply to you. Keep your account secure and contact support@recastfx.com if you suspect unauthorized access.",
  ],
  [
    "Your content and permissions",
    "You must own or hold all permissions needed to upload, edit and process your clips, images, audio and prompts, including copyright, privacy and likeness permissions from people shown. You keep whatever rights you hold in your uploads. You allow RecastFX and its service providers to process, store and transmit that material only as needed to provide and support your requested service. Rights in generated content also depend on applicable law and the generation provider’s terms; we do not guarantee exclusive rights or copyright protection.",
  ],
  [
    "Acceptable use",
    "Do not use RecastFX to infringe rights, impersonate people deceptively, harass, defraud, create non-consensual intimate imagery, exploit minors or create unlawful content. Do not present altered footage as authentic when doing so would mislead or harm others. We may refuse jobs or restrict accounts involved in abuse or risks to the service.",
  ],
  [
    "Credits and generation",
    "The app displays the credit cost for your selected clip before generation. Confirm the duration, characters and settings before submitting. Credits are used for app services, are not currency and have no cash redemption value. Checkout shows the pack, price and payment terms before purchase. Processing may fail or be delayed, and output quality is not guaranteed. Contact support about billing errors or failed jobs. Refund and consumer rights required by applicable law are not excluded by these Terms.",
  ],
  [
    "Your library",
    "Finished videos are kept for 30 days. Download videos you want to retain; the library is not permanent storage. Jobs may continue after you close the app. Notifications depend on device permissions and connectivity and are not a guarantee of delivery.",
  ],
  [
    "Availability and responsibility",
    "The service may change or be interrupted for maintenance, capacity limits or provider outages. You are responsible for reviewing outputs before publishing or relying on them. To the extent permitted by applicable law, the service is provided as available without a guarantee that every output will meet your expectations. Nothing in these Terms limits rights or liabilities that cannot lawfully be limited.",
  ],
  [
    "Support and changes",
    "Contact support@recastfx.com for account, content, payment or policy concerns. We may update these Terms as the service evolves; the effective date appears below. Material changes will be communicated through the app where appropriate.",
  ],
];

export function LegalPage({
  document,
  onBack,
}: {
  document: LegalDocument;
  onBack: () => void;
}) {
  const privacy = document === "privacy";
  return (
    <Box
      component="main"
      sx={{
        maxWidth: 820,
        mx: "auto",
        px: { xs: 2.5, md: 4 },
        py: { xs: 4, md: 7 },
      }}
    >
      <Button onClick={onBack} sx={{ px: 0, mb: 2 }}>
        Back to RecastFX
      </Button>
      <Typography component="h1" variant="h3" fontWeight={750}>
        {privacy ? "Privacy Policy" : "Terms of Service"}
      </Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mt: 1, mb: 4 }}>
        Effective October 19, 2026
      </Typography>
      <Stack spacing={3.5}>
        {(privacy ? PRIVACY : TERMS).map(([heading, content]) => (
          <Box key={heading}>
            <Typography component="h2" variant="h6" sx={{ mb: 1 }}>
              {heading}
            </Typography>
            <Typography
              color="text.secondary"
              sx={{ lineHeight: 1.8, overflowWrap: "anywhere" }}
            >
              {content}
            </Typography>
          </Box>
        ))}
      </Stack>
    </Box>
  );
}
