/**
 * The status of a body being built after Finish or a pull (ADR-0039), in words: what is built, the depth the
 * pull has, what the run says, and the time so far. Pure: the tests run in Node.
 */
import { depthLabel } from "./pull";

export interface BuildStatusInput {
  /** The run's own line ("Sketch · Pull: meshing 40 %"), or "" before it has said anything. */
  progress: string;
  /** Seconds since the build was asked for. */
  seconds: number;
  /** The depth the ghost shows, in mm. */
  depth: number;
  /** False until `operation.create` has answered with the operation's id. */
  known: boolean;
}

/** "Building the body · pull 25 mm · 4 s", or the run's own line with the time once it reports. */
export function buildStatusText(input: BuildStatusInput): string {
  const whole = Math.floor(Math.max(0, input.seconds));
  const time = whole >= 1 ? ` · ${whole < 60 ? `${whole} s` : `${Math.floor(whole / 60)} min ${whole % 60} s`}` : "";
  const pull = Number.isFinite(input.depth) && input.depth > 0 ? ` · pull ${depthLabel(input.depth)} mm` : "";
  if (!input.known) return `Creating the body${pull}${time}`;
  if (input.progress !== "") return `${input.progress.replace(/…$/, "")}${pull}${time}`;
  return `Building the body${pull}${time}`;
}

/** The toast after Finish: the depth it chose and the two ways to change it. */
export function finishToastText(depth: number): string {
  return `Pulled ${depthLabel(depth)} mm. Drag the arrow on the body, or type a depth.`;
}
