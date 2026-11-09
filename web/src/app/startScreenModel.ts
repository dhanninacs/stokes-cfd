/**
 * The Start screen's small rules, pure so they are tested without a DOM: how old a project's last change is, in
 * the few words a card has room for, and the folder a project lives in, which tells two projects of the same
 * name apart (measured: the recent list held two `pipe3` rows that only their folders distinguish).
 */

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/**
 * When something happened, relative to `now`: "just now", "5 min ago", "3 h ago", "yesterday", "4 d ago", and after
 * a week the date ("12 Oct", with the year when it is not this one). "" for a stamp that does not parse, so a card
 * shows nothing rather than "NaN".
 */
export function ago(stamp: string | undefined, now: number = Date.now()): string {
  if (stamp === undefined || stamp === "") return "";
  const then = Date.parse(stamp);
  if (!Number.isFinite(then)) return "";
  const gap = Math.max(0, now - then);
  if (gap < MINUTE) return "just now";
  if (gap < HOUR) return `${Math.floor(gap / MINUTE)} min ago`;
  if (gap < DAY) return `${Math.floor(gap / HOUR)} h ago`;
  if (gap < 2 * DAY) return "yesterday";
  if (gap < 7 * DAY) return `${Math.floor(gap / DAY)} d ago`;
  const date = new Date(then);
  const year = new Date(now).getFullYear() === date.getFullYear() ? "" : ` ${date.getFullYear()}`;
  return `${date.getDate()} ${MONTHS[date.getMonth()]}${year}`;
}

/**
 * The folder a project sits in, as a card shows it: the parent's name (the `.stokes` folder itself is the project),
 * or "" for a path with no parent. Both slash styles are read, since a Windows path arrives with backslashes.
 */
export function folderOf(path: string): string {
  const parts = path.split(/[\\/]+/).filter(Boolean);
  return parts.length >= 2 ? parts[parts.length - 2]! : "";
}

/** The recent projects a card grid shows: the existing ones first, then the missing, each in the order given, at most `max`. */
export function recentCards<T extends { exists: boolean }>(rows: readonly T[], max = 8): T[] {
  return [...rows.filter((r) => r.exists), ...rows.filter((r) => !r.exists)].slice(0, max);
}

/**
 * Two characters that tell a card apart when it has no thumbnail: the first letters of the first two words
 * (camelCase and separators both split: `angledDuctLTS` is AD, `hot_room` HR), else the first letter and a
 * trailing number (`pipe3` is P3), else the first two letters (`cavity` is CA). Upper case; "" for no letters.
 */
export function monogram(name: string): string {
  const words = name.replace(/([a-z0-9])([A-Z])/g, "$1 $2").split(/[^A-Za-z0-9]+/).filter(Boolean);
  if (words.length === 0) return "";
  if (words.length >= 2) return (words[0]![0]! + words[1]![0]!).toUpperCase();
  const word = words[0]!;
  const digits = /(\d{1,2})$/.exec(word)?.[1];
  if (digits !== undefined && word.length > digits.length) return (word[0]! + digits).toUpperCase();
  return word.slice(0, 2).toUpperCase();
}
