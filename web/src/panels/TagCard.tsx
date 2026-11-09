import { useEffect, useState } from "react";
import type { NodeSummary } from "../api/types";
import { useStore } from "../state/store";

/**
 * A tag's page: what it is, which faces it holds, and who uses it (Claude's review item 5).
 *
 * **The raw JSON must never reach a page.** The page rendered the `members` parameter, so a user saw
 * `[{"part":"n_1364…","faces":[[2,2]],"basis":"2437…"}]` where a sentence belongs - and `[[2,2]]` is an
 * *inclusive face-id range*, which is not something a person reads. `tag.members` returns the same
 * thing **expanded**: one entry per part with its name and how many faces it holds.
 *
 * **One line per part, and clicking one selects it**, which is what makes the page a way through the
 * model rather than a summary of it.
 */
interface Member {
  part: string;
  path?: string;
  name?: string;
  /** Faces this tag holds on that part. */
  count?: number;
  /** Faces the part has in total, for "3 of 6". */
  part_faces?: number;
  /** True when the tag holds the whole part (a region's membership), not a face set. */
  whole?: boolean;
  /** True when the part is no longer in the project. */
  exists?: boolean;
}

/** What a tag's kind means, in the words the plan uses. */
const KIND_WORDS: Record<string, string> = {
  boundary: "a mesh patch: one side of the fluid",
  region: "a whole body: the cells inside it",
  plain: "a working set, used by operations and picks",
};

export default function TagCard({ node }: { node: NodeSummary }) {
  const execute = useStore((s) => s.execute);
  const select = useStore((s) => s.select);
  const [members, setMembers] = useState<Member[] | null>(null);
  const [kind, setKind] = useState<string>("");

  useEffect(() => {
    let live = true;
    void execute("tag.members", { tag: node.id }, { silent: true })
      .then((reply) => {
        const answer = reply as { members?: Member[]; kind?: string };
        if (!live) return;
        setMembers(answer.members ?? []);
        setKind(answer.kind ?? "");
      })
      .catch(() => live && setMembers([]));
    return () => {
      live = false;
    };
  }, [execute, node.id, node.params]);

  const total = (members ?? []).reduce((sum, m) => sum + (m.count ?? 0), 0);

  return (
    <div className="flex flex-col gap-2" data-ws="tag-card">
      <p className="text-[12.5px]">
        {members === null
          ? "Reading the faces…"
          : total === 0
            ? "No faces yet."
            : `${total} ${total === 1 ? "face" : "faces"} on ${members.length} ${
                members.length === 1 ? "body" : "bodies"
              }.`}
        {kind !== "" && <span className="muted"> {KIND_WORDS[kind] ?? kind}.</span>}
      </p>
      {/*
        **One line per part**, with "3 of 6" rather than a bare 3: the count alone does not say whether
        the tag holds most of a body or a sliver of it, which is the question a user is asking.
      */}
      {(members ?? []).map((m) => (
        <button
          key={m.part}
          type="button"
          className="flex items-center gap-2 py-1 text-left text-[12.5px] hover:underline"
          data-ws="tag-member"
          title={m.path}
          // **Clicking selects the body**, so the pane follows to its page.
          onClick={() => select(m.part)}
        >
          <span className="min-w-0 flex-1 truncate">
            {m.whole === true ? "whole body" : `${m.count ?? 0} of ${m.part_faces ?? "?"} faces`}
          </span>
          <span className="muted shrink-0">{m.name ?? m.part}</span>
          {m.exists === false && (
            <span className="shrink-0" style={{ color: "var(--danger)" }}>
              gone
            </span>
          )}
        </button>
      ))}
    </div>
  );
}
