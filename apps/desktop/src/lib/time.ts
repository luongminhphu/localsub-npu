/** Subtitle time helpers. Display format is HH:MM:SS.mmm (dot, like WebVTT). */

export function formatTime(ms: number): string {
  const safe = Math.max(0, Math.round(ms));
  const h = Math.floor(safe / 3_600_000);
  const m = Math.floor((safe % 3_600_000) / 60_000);
  const s = Math.floor((safe % 60_000) / 1000);
  const milli = safe % 1000;
  const pad = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${pad(h)}:${pad(m)}:${pad(s)}.${pad(milli, 3)}`;
}

/**
 * Parse "HH:MM:SS.mmm", "MM:SS.mmm", "SS.mmm" (comma or dot) into milliseconds.
 * Returns null for anything that is not a valid non-negative time.
 */
export function parseTime(value: string): number | null {
  const match = /^\s*(?:(\d{1,2}):)?(?:(\d{1,2}):)?(\d{1,2})(?:[.,](\d{1,3}))?\s*$/.exec(value);
  if (!match) return null;
  const [, a, b, sec, frac] = match;
  const hours = b !== undefined ? Number(a) : 0;
  const minutes = b !== undefined ? Number(b) : a !== undefined ? Number(a) : 0;
  const seconds = Number(sec);
  if (minutes > 59 || seconds > 59) return null;
  const milli = frac ? Number(frac.padEnd(3, "0")) : 0;
  return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milli;
}
