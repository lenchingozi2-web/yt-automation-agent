/** Single-production limits shared by the Studio composer, parser, and API. */
export const MIN_VIDEO_SECONDS = 60;
export const MAX_VIDEO_SECONDS = 30 * 60;
export const MAX_VIDEO_SCENES = 160;
export const MAX_VIDEO_MINUTES = MAX_VIDEO_SECONDS / 60;
export const VIDEO_DURATION_OPTIONS = [60, 180, 300, 600, 720, 1200, 1800] as const;

export function validVideoDuration(value: unknown): value is number {
  return typeof value === "number" && Number.isInteger(value) && value >= MIN_VIDEO_SECONDS && value <= MAX_VIDEO_SECONDS;
}
