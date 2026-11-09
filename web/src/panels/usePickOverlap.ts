/**
 * The boundaries the live pick already overlaps, asked of the server (`boundary.overlap`) while the
 * pick changes, so the set-up flow can warn before an assign rather than after `setup.check`.
 *
 * Read through `client.call`, not `execute`: this is a query asked on every pick change, and `execute`
 * would log it to the Console and schedule a `setup.check` each time. A short delay coalesces a
 * paint stroke or a box into one question; an answer for a pick that has since changed is dropped.
 * Several surfaces show the answer at once (the context toolbar, the Assign menu, the value panel,
 * the pick card), so one question per pick and tree is shared between them rather than one each.
 */

import { useEffect, useMemo, useState } from "react";
import type { StokesClient } from "../api/client";
import type { BoundaryOverlapAnswer, NodeSummary } from "../api/types";
import { pickAsMembers, useStore, type PickMember } from "../state/store";

/** How long the pick must rest before it is asked about, in ms (a stroke reports many picks). */
const SETTLE_MS = 120;

/** The last question asked, shared by every hook instance: same pick and same tree, same answer. */
let lastAsked: { key: string; tree: NodeSummary | null; answer: Promise<BoundaryOverlapAnswer | null> } | null = null;

function ask(client: StokesClient, members: PickMember[], key: string, tree: NodeSummary | null): Promise<BoundaryOverlapAnswer | null> {
  if (lastAsked !== null && lastAsked.key === key && lastAsked.tree === tree) return lastAsked.answer;
  // A pick the server cannot read (a part removed meanwhile) has nothing to warn about.
  const answer = client.call<BoundaryOverlapAnswer>("boundary.overlap", { members }).catch(() => null);
  lastAsked = { key, tree, answer };
  return answer;
}

export function usePickOverlap(): BoundaryOverlapAnswer | null {
  const picked = useStore((s) => s.picked);
  const pickMode = useStore((s) => s.pickMode);
  const client = useStore((s) => s.client);
  const project = useStore((s) => s.project);
  // The tree changes when a boundary is assigned or removed, which changes the answer for the same pick.
  const tree = useStore((s) => s.tree);
  const members = useMemo(() => pickAsMembers(picked, pickMode)?.members ?? null, [picked, pickMode]);
  const key = members === null ? "" : JSON.stringify(members);
  const [answer, setAnswer] = useState<{ key: string; value: BoundaryOverlapAnswer | null } | null>(null);

  useEffect(() => {
    if (client === null || project === null || members === null || members.length === 0) return;
    let live = true;
    const timer = window.setTimeout(() => {
      void ask(client, members, key, tree).then((value) => {
        if (live) setAnswer({ key, value });
      });
    }, SETTLE_MS);
    return () => {
      live = false;
      window.clearTimeout(timer);
    };
    // `key` stands for `members` (a new array each render); `tree` re-asks after an assign.
  }, [client, project, key, tree]);

  return answer !== null && answer.key === key && key !== "" ? answer.value : null;
}
