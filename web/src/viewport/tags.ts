/**
 * Tags in the viewport (ADR-0021 3.5 and 4.1): every `visible` tag draws its faces in its `color` at
 * its `opacity`, and every tag whose eye is off hides its faces.
 *
 * The colour takes the ADR's switch to a per-vertex tint (materials.ts) from the start. One overlay
 * mesh per tag, drawn over the parts, was measured first: with one tag over every face of the
 * 4,500-part benchmark it doubled the triangles drawn per frame and added 10.6 ms to the GPU frame,
 * against a budget of 1 ms. The tint draws nothing of its own, so neither the number of tags nor
 * their size reaches the frame cost.
 *
 * `TagOverlays` turns the tree's `tag` nodes and their expanded members (`tag.members`, fetched when
 * a tag appears or its `members` change) into per-part state and hands it to the scene, which keeps
 * it per source and applies it to every mesh the source gets, coarse or fine, batched or not. Where
 * tags overlap, the one further down the tree shows; a face that a hidden tag holds is hidden
 * whatever else holds it. A whole-part member colours the whole part and hides nothing per face (the
 * part's own eye is for that). Hidden and isolated parts are not drawn, so their colours do not
 * matter. Rebuilds coalesce to one per animation frame. The colours are the user's: theme changes do
 * not touch them.
 */

import type { NodeSummary, TagMembers } from "../api/types";
import { parseHexColor } from "./materials";
import { packTint, type FaceTint } from "./scene";

/** What the tree says about one `tag` node: its id and the parameters drawn from. */
export interface TagInfo {
  id: string;
  /** `#rrggbb` from the `color` parameter. */
  color: string;
  /** The `visible` parameter (the tag's eye). */
  visible: boolean;
  /** The `opacity` parameter, clamped to 0..1: the strength of the colour over the part's own. */
  opacity: number;
  /** The `members` parameter as stored; a change re-fetches the faces. */
  members: string;
}

/** The scene's side: per-part state, kept by the scene for the part's meshes to come. */
export interface TagHost {
  setFaceTint(part: string, tint: FaceTint | null): void;
  setHiddenFaces(part: string, faces: ReadonlySet<number>): void;
}

/** The tag type's default colour (the first of the server's tag palette), for a malformed `color`. */
const FALLBACK_RGB: [number, number, number] = [0x3b / 255, 0x82 / 255, 0xc4 / 255];
const NO_FACES: ReadonlySet<number> = new Set();
/** Where tags live; anything outside it is not a tag the viewport draws. */
const TAGS_PATH = "/geometry/tags";

/** The `tag` nodes under /geometry/tags, in tree order. */
export function collectTags(tree: NodeSummary | null): TagInfo[] {
  const out: TagInfo[] = [];
  const visit = (node: NodeSummary): void => {
    if (node.type === "tag") {
      const params = node.params;
      out.push({
        id: node.id,
        color: typeof params.color === "string" ? params.color : "",
        visible: params.visible !== false,
        opacity: typeof params.opacity === "number" ? Math.min(1, Math.max(0, params.opacity)) : 1,
        members: typeof params.members === "string" ? params.members : JSON.stringify(params.members ?? []),
      });
    }
    for (const child of node.children ?? []) visit(child);
  };
  const tags = tree?.children?.find((n) => n.path === "/geometry")?.children?.find((n) => n.path === TAGS_PATH);
  if (tags) visit(tags);
  return out;
}

interface TagState {
  info: TagInfo;
  /** The expanded members of `info.members`; null until the fetch answers (or when it failed). */
  members: TagMembers | null;
  /** Counts the members texts seen: an answer for an older one is dropped. */
  generation: number;
}

/** A part's tint under construction: the whole-part word and the per-face words over it. */
interface TintDraft {
  all: number;
  faces: Map<number, number>;
}

export class TagOverlays {
  private readonly tags = new Map<string, TagState>();
  /** Tree order of the tags, for the overlaps. */
  private order: string[] = [];
  /** What the host was last given, per part, so a rebuild pushes only what moved. */
  private readonly pushedTint = new Map<string, FaceTint>();
  private readonly pushedHidden = new Map<string, ReadonlySet<number>>();
  private frame = 0;
  private disposed = false;

  constructor(
    private readonly host: TagHost,
    /** Fetches a tag's expanded members (`tag.members`); null when the server refused. */
    private readonly load: (id: string) => Promise<TagMembers | null>,
  ) {}

  /** The tags the tree shows now. A tag whose `members` changed fetches its faces again; any other change is drawn at once. */
  setTags(infos: readonly TagInfo[]): void {
    if (this.disposed) return;
    let dirty = false;
    const seen = new Set<string>();
    for (const info of infos) {
      seen.add(info.id);
      const state = this.tags.get(info.id);
      if (state === undefined) {
        this.tags.set(info.id, { info, members: null, generation: 0 });
        this.fetch(info.id);
        continue;
      }
      const before = state.info;
      state.info = info;
      if (before.members !== info.members) {
        state.generation++;
        this.fetch(info.id); // the old faces stay drawn until the new ones arrive
      } else if (before.color !== info.color || before.visible !== info.visible || before.opacity !== info.opacity) {
        dirty = true;
      }
    }
    for (const id of this.tags.keys()) {
      if (seen.has(id)) continue;
      this.tags.delete(id);
      dirty = true;
    }
    const order = infos.map((info) => info.id);
    if (order.length !== this.order.length || order.some((id, i) => id !== this.order[i])) {
      this.order = order;
      dirty = true;
    }
    if (dirty) this.schedule();
  }

  /** Takes every colour and hidden face back off the scene's parts. */
  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (this.frame) cancelAnimationFrame(this.frame);
    for (const part of this.pushedTint.keys()) this.host.setFaceTint(part, null);
    for (const part of this.pushedHidden.keys()) this.host.setHiddenFaces(part, NO_FACES);
    this.pushedTint.clear();
    this.pushedHidden.clear();
    this.tags.clear();
  }

  private fetch(id: string): void {
    const state = this.tags.get(id);
    if (state === undefined) return;
    const generation = state.generation;
    void this.load(id).then((members) => {
      const now = this.tags.get(id);
      if (this.disposed || now !== state || now.generation !== generation) return;
      now.members = members;
      this.schedule();
    });
  }

  private schedule(): void {
    if (this.disposed || this.frame) return;
    this.frame = requestAnimationFrame(() => {
      this.frame = 0;
      if (!this.disposed) this.rebuild();
    });
  }

  /** Every tag's faces as per-part tints and hidden sets; the host gets the parts that moved. */
  private rebuild(): void {
    const tints = new Map<string, TintDraft>();
    const hidden = new Map<string, Set<number>>();
    for (const id of this.order) {
      const state = this.tags.get(id);
      if (!state?.members) continue;
      const { visible, opacity } = state.info;
      const word = visible ? packTint(parseHexColor(state.info.color) ?? FALLBACK_RGB, opacity) : 0;
      if (visible && word === 0) continue; // opacity 0: the tag draws nothing, over nothing
      for (const member of state.members) {
        if (!member.exists) continue;
        if (!visible) {
          if (member.whole || member.faces.length === 0) continue;
          let faces = hidden.get(member.part);
          if (!faces) hidden.set(member.part, (faces = new Set()));
          for (const face of member.faces) faces.add(face);
          continue;
        }
        // Later tags draw over earlier ones, a whole part over every face before it.
        let draft = tints.get(member.part);
        if (member.whole) {
          tints.set(member.part, { all: word, faces: new Map() });
          continue;
        }
        if (!draft) tints.set(member.part, (draft = { all: 0, faces: new Map() }));
        for (const face of member.faces) draft.faces.set(face, word);
      }
    }
    for (const part of this.pushedTint.keys()) {
      if (tints.has(part)) continue;
      this.host.setFaceTint(part, null);
      this.pushedTint.delete(part);
    }
    for (const [part, draft] of tints) {
      const previous = this.pushedTint.get(part);
      if (previous && sameTint(previous, draft)) continue;
      this.host.setFaceTint(part, draft);
      this.pushedTint.set(part, draft);
    }
    for (const part of this.pushedHidden.keys()) {
      if (hidden.has(part)) continue;
      this.host.setHiddenFaces(part, NO_FACES);
      this.pushedHidden.delete(part);
    }
    for (const [part, faces] of hidden) {
      const previous = this.pushedHidden.get(part);
      if (previous && sameSet(previous, faces)) continue;
      this.host.setHiddenFaces(part, faces);
      this.pushedHidden.set(part, faces);
    }
  }
}

function sameTint(a: FaceTint, b: FaceTint): boolean {
  if (a.all !== b.all || a.faces.size !== b.faces.size) return false;
  for (const [face, word] of b.faces) if (a.faces.get(face) !== word) return false;
  return true;
}

function sameSet(a: ReadonlySet<number>, b: ReadonlySet<number>): boolean {
  if (a.size !== b.size) return false;
  for (const x of b) if (!a.has(x)) return false;
  return true;
}
