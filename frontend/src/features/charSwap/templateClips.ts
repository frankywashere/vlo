import beforeVideo from "./assets/showcase-before.mp4";
import afterVideo from "./assets/showcase-after.mp4";
import beforePoster from "./assets/showcase-before.jpg";
import afterPoster from "./assets/showcase-after.jpg";
import kitchenVideo from "./assets/showcase-kitchen-comparison.mp4";
import kitchenPoster from "./assets/showcase-kitchen-comparison.jpg";

export const TEMPLATE_CATEGORIES = [
  "All",
  "Cinematic",
  "Social",
  "Characters",
  "Comedy",
] as const;
export type TemplateCategory = (typeof TEMPLATE_CATEGORIES)[number];
export interface TemplateClip {
  id: string;
  title: string;
  category: Exclude<TemplateCategory, "All">;
  video: string;
  poster: string;
  position: string;
}
export const TEMPLATE_CLIPS: TemplateClip[] = [
  {
    id: "closeup",
    title: "The close-up",
    category: "Cinematic",
    video: beforeVideo,
    poster: beforePoster,
    position: "37% center",
  },
  {
    id: "reaction",
    title: "A big reaction",
    category: "Social",
    video: beforeVideo,
    poster: beforePoster,
    position: "center center",
  },
  {
    id: "new-character",
    title: "An unexpected guest",
    category: "Characters",
    video: afterVideo,
    poster: afterPoster,
    position: "37% center",
  },
  {
    id: "comedy",
    title: "The double take",
    category: "Comedy",
    video: afterVideo,
    poster: afterPoster,
    position: "45% center",
  },
  {
    id: "movie-moment",
    title: "The movie moment",
    category: "Cinematic",
    video: beforeVideo,
    poster: beforePoster,
    position: "45% center",
  },
  {
    id: "character-portrait",
    title: "A familiar face",
    category: "Characters",
    video: afterVideo,
    poster: afterPoster,
    position: "center center",
  },
  {
    id: "grand-entrance",
    title: "The grand entrance",
    category: "Cinematic",
    video: beforeVideo,
    poster: beforePoster,
    position: "25% center",
  },
  {
    id: "kitchen-cameo",
    title: "A kitchen cameo",
    category: "Comedy",
    video: kitchenVideo,
    poster: kitchenPoster,
    position: "center 30%",
  },
  {
    id: "caught-on-camera",
    title: "Caught on camera",
    category: "Social",
    video: beforeVideo,
    poster: beforePoster,
    position: "65% center",
  },
  {
    id: "new-lead",
    title: "Meet the new lead",
    category: "Characters",
    video: afterVideo,
    poster: afterPoster,
    position: "25% center",
  },
  {
    id: "unexpected-roommate",
    title: "An unexpected roommate",
    category: "Characters",
    video: kitchenVideo,
    poster: kitchenPoster,
    position: "right 30%",
  },
  {
    id: "quiet-moment",
    title: "A quiet moment",
    category: "Cinematic",
    video: beforeVideo,
    poster: beforePoster,
    position: "55% center",
  },
  {
    id: "plot-twist",
    title: "The plot twist",
    category: "Comedy",
    video: afterVideo,
    poster: afterPoster,
    position: "65% center",
  },
  {
    id: "scene-stealer",
    title: "The scene stealer",
    category: "Social",
    video: kitchenVideo,
    poster: kitchenPoster,
    position: "left 30%",
  },
  {
    id: "final-look",
    title: "One last look",
    category: "Cinematic",
    video: afterVideo,
    poster: afterPoster,
    position: "55% center",
  },
];
