/**
 * The Prepare menu's entries as data (T-029): one entry per geometry operation the menu offers,
 * with a label, an action id, the operation type it opens, whether the live pick fits, and a `why`
 * when it does not. The menu component (`app/PrepareMenu.tsx`) only renders this list, so ADR-0022
 * 10.2 adds "External flow…" and "Internal flow…" above Enclosure… in T-050 by adding two entries
 * here, with no change to the menu.
 *
 * Pure data and pure functions: the tests run in Node (`npm.cmd test`).
 */

/** What the pick can do: what the entries are enabled by. */
export interface PrepareOptions {
  /** Parts the pick holds that an operation can read (`readablePick`; the sample part is not one). */
  bodies: number;
  /** Face ids the pick holds (face mode). */
  faces: number;
  /** The operation types the server has, by type (`operation.types`); empty while it did not answer. */
  built?: Record<string, boolean>;
}

/**
 * One entry of the Prepare menu. `action` is the action id the entry belongs to: one of the four
 * `prepare.*` ids the workspace registers. A leaf names the id of the submenu it lives in, so a
 * leaf that T-050 adds can carry its own id without changing the menu's shape.
 */
export interface PrepareEntry {
  id: string;
  label: string;
  action: string;
  /** The operation type the entry opens; absent on one that opens a submenu. */
  op?: string;
  /** Parameter values the card starts from (a boolean's `op`). */
  params?: Record<string, unknown>;
  /** The leaves of a submenu, in order. */
  entries?: readonly PrepareEntry[];
  enabled: boolean;
  /** Why the entry is disabled, shown on the item and in the button's tooltip. */
  why?: string;
}

/**
 * The two flow helpers' action ids (ADR-0022 §10.2, T-050). They are not operation types: each opens
 * a card that runs an operation and then offers the default conditions for it.
 */
export const EXTERNAL_FLOW_ACTION = "prepare.externalFlow";
export const INTERNAL_FLOW_ACTION = "prepare.internalFlow";

/** Why an entry with no pick of its own is disabled. */
export const PICK_BODY = "Pick a body first";
export const PICK_TWO_BODIES = "Boolean needs two bodies: Shift-click a second one";
export const PICK_FACES = "Remove faces needs faces: switch to Face and click them";
/** What the server says of a type whose module is not built yet; `operation.create` refuses it too. */
export const NOT_BUILT = "Not built yet: it arrives in a later task";

/** The entries with their pick rules, and the `built` of each type the server has. */
export function prepareEntries(options: PrepareOptions): PrepareEntry[] {
  const { bodies, faces, built = {} } = options;
  const body = (count: number): boolean => bodies >= count;
  const anyBody = body(1);
  const twoBodies = body(2);

  /** A leaf: enabled by the pick, and refused as a whole while its module is not built yet. */
  const leaf = (id: string, label: string, action: string, op: string, enabled: boolean, why?: string, params?: Record<string, unknown>): PrepareEntry => {
    const absent = built[op] === false;
    return { id, label, action, op, params, enabled: enabled && !absent, why: absent ? NOT_BUILT : why };
  };

  return [
    // ADR-0022 §10.2 puts the two flow helpers above Enclosure…: they are the common way in, and the
    // plain operations below stay the manual route.
    {
      id: "external-flow",
      label: "External flow…",
      action: EXTERNAL_FLOW_ACTION,
      // The enclosure needs a body, exactly as the plain Enclosure… does; the card adds a velocity
      // field and the "add default conditions" switch on top.
      enabled: anyBody && built.enclosure !== false,
      why: built.enclosure === false ? NOT_BUILT : PICK_BODY,
    },
    {
      id: "internal-flow",
      label: "Internal flow…",
      action: INTERNAL_FLOW_ACTION,
      // Extract fluid needs a body and, on its card, a seed point.
      enabled: anyBody && built.extract_fluid !== false,
      why: built.extract_fluid === false ? NOT_BUILT : PICK_BODY,
    },
    leaf("enclosure", "Enclosure…", "prepare.enclosure", "enclosure", anyBody, PICK_BODY),
    leaf("extract-fluid", "Extract fluid…", "prepare.extractFluid", "extract_fluid", anyBody, PICK_BODY),
    {
      id: "boolean",
      label: "Boolean",
      action: "prepare.boolean",
      enabled: twoBodies,
      why: PICK_TWO_BODIES,
      entries: [
        leaf("fuse", "Fuse", "prepare.boolean", "boolean", twoBodies, PICK_TWO_BODIES, { op: "fuse" }),
        leaf("cut", "Cut", "prepare.boolean", "boolean", twoBodies, PICK_TWO_BODIES, { op: "cut" }),
        leaf("common", "Common", "prepare.boolean", "boolean", twoBodies, PICK_TWO_BODIES, { op: "common" }),
      ],
    },
    {
      id: "repair",
      label: "Repair",
      action: "prepare.repair",
      enabled: anyBody || faces > 0,
      why: "Repair needs a body, or the faces to remove",
      entries: [
        leaf("heal", "Heal", "prepare.repair", "heal", anyBody, PICK_BODY),
        leaf("sew", "Sew", "prepare.repair", "sew", anyBody, PICK_BODY),
        leaf("cap", "Close openings", "prepare.repair", "cap", anyBody, PICK_BODY),
        leaf("unify", "Unify faces", "prepare.repair", "unify", anyBody, PICK_BODY),
        leaf("slivers", "Drop slivers", "prepare.repair", "slivers", anyBody, PICK_BODY),
        // Removing faces is the one entry that reads a face pick; the card fills its `faces`
        // parameter from the pick (`operationCreateParams`), which is why the pick must be faces.
        leaf("defeature", "Remove faces", "prepare.repair", "defeature", faces > 0, PICK_FACES),
        leaf("orient", "Fix orientation", "prepare.repair", "orient", anyBody, PICK_BODY),
        leaf("solidify", "Make solid", "prepare.repair", "solidify", anyBody, PICK_BODY),
      ],
    },
  ];
}
