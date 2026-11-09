import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import SchemaFields, { type SchemaFieldsProps } from "./SchemaFields.tsx";
import { groupSettingsFields, searchSettingsGroups, type SettingsSectionSpec } from "./settingsPresentation";
import "./SettingsFields.css";
export type { SettingsSectionSpec, GroupedSettingsField } from "./settingsPresentation";

export interface SettingsFieldsProps extends SchemaFieldsProps {
  sections: readonly SettingsSectionSpec[];
  readOnlyFields?: Readonly<Record<string, string>>;
  renderField?: (name: string) => ReactNode | undefined;
  initialSection?: string;
  /**
   * `full` (the default) is the category select, the search and "Show all sections". `search` is the Advanced
   * disclosure's own page: the sections are all listed (closed except the Advanced ones that matter) and the
   * only tool is the search, because a category picker beside a list of every section is the same choice twice.
   */
  tools?: "full" | "search";
}

export { groupSettingsFields } from "./settingsPresentation";

export default function SettingsFields({ sections, readOnlyFields, renderField, initialSection, tools = "full", ...fieldProps }: SettingsFieldsProps) {
  const groups = useMemo(
    () => groupSettingsFields(fieldProps.properties, sections, fieldProps.include),
    [fieldProps.properties, fieldProps.include, sections],
  );
  const searchOnly = tools === "search";
  // The section a finding or a task row names opens, in the search-only layout where every section is listed.
  const initial = Object.fromEntries(groups.map((group) => [group.id, group.defaultOpen || (searchOnly && group.id === initialSection)]));
  const [open, setOpen] = useState<Record<string, boolean>>(initial);
  const [query, setQuery] = useState("");
  const [category, setCategory] = useState(initialSection ?? groups[0]?.id ?? "");
  const [allSections, setAllSections] = useState(searchOnly);
  const activeCategory = groups.some((group) => group.id === category) ? category : groups[0]?.id ?? "";
  const shown = searchSettingsGroups(groups, fieldProps.properties, query, fieldProps.errors);
  const previousErrorSignatures = useRef<Record<string, string>>({});

  useEffect(() => {
    setOpen((current) => {
      const next = { ...current };
      for (const group of groups) if (!(group.id in next)) next[group.id] = group.defaultOpen;
      return next;
    });
  }, [groups]);

  useEffect(() => {
    const nextSignatures: Record<string, string> = {};
    for (const group of groups) {
      const signature = group.fields
        .map((name) => [name, fieldProps.errors?.[name] ?? ""] as const)
        .filter(([, message]) => message !== "")
        .map(([name, message]) => `${name}:${message}`)
        .join("|");
      nextSignatures[group.id] = signature;
      if (signature && previousErrorSignatures.current[group.id] !== signature) {
        setOpen((current) => ({ ...current, [group.id]: true }));
        setCategory(group.id);
      }
    }
    previousErrorSignatures.current = nextSignatures;
  }, [fieldProps.errors, groups]);

  return (
    <div className="settings-fields">
      <div className="settings-fields__tools" role="group" aria-label="Find and expand settings">
        {!searchOnly && (
          <label className="settings-fields__catalog-label"><span>Settings category</span>
            <select className="field" aria-label="Settings category" value={activeCategory} onChange={(event) => { setCategory(event.target.value); setAllSections(false); setQuery(""); }}>
              {groups.map((group) => <option key={group.id} value={group.id}>{group.title}{group.fields.some((name) => Boolean(fieldProps.errors?.[name])) ? " — needs attention" : ""}</option>)}
            </select>
          </label>
        )}
        <input type="search" className="field" aria-label="Search settings" placeholder="Find a setting…" value={query} onChange={(event) => setQuery(event.target.value)} />
        {!searchOnly && (
          <div className="settings-fields__actions">
            <button type="button" className="link no-drag" onClick={() => { setQuery(""); setAllSections(!allSections); setOpen(initial); }}>{allSections ? "Show one category" : "Show all sections"}</button>
          </div>
        )}
      </div>
      {shown.length === 0 && <p className="muted" role="status" title="Try a section name or clear the search." aria-description="Try a section name or clear the search.">No settings match “{query}”</p>}
      {shown.map((group) => (
        <div key={group.id} hidden={!allSections && query.trim() === "" && group.id !== activeCategory && !group.fields.some((name) => Boolean(fieldProps.errors?.[name]))}>
        <details
          className="settings-fields__section"
          key={group.id}
          open={(!allSections && group.id === activeCategory) || query.trim() !== "" || group.fields.some((name) => Boolean(fieldProps.errors?.[name])) || (open[group.id] ?? group.defaultOpen)}
          onToggle={(event) => {
            // Search opens matching sections temporarily; do not overwrite the user's disclosure state.
            if (query.trim() !== "" || group.fields.some((name) => Boolean(fieldProps.errors?.[name]))) return;
            const nextOpen = event.currentTarget.open;
            setOpen((current) => (current[group.id] === nextOpen ? current : { ...current, [group.id]: nextOpen }));
          }}
        >
          {/* The section's sentence is the summary's tooltip, not a paragraph under it. */}
          <summary className="settings-fields__summary" title={group.description} aria-description={group.description}>
            <span className="settings-fields__title">{group.title}</span>
            {group.fields.some((name) => Boolean(fieldProps.errors?.[name])) && <span className="settings-fields__error">Needs attention</span>}
            {!group.defaultOpen && <span className="settings-fields__optional">More options</span>}
          </summary>
          <div className="settings-fields__body">
            {group.fields.map((name) => {
              const reason = readOnlyFields?.[name];
              const custom = renderField?.(name);
              return (
                <div className="settings-fields__field" key={name} data-settings-field={name}>
                  {custom !== undefined ? (
                    custom
                  ) : (
                    <SchemaFields
                      {...fieldProps}
                      properties={{ [name]: fieldProps.properties[name] }}
                      disabled={fieldProps.disabled || reason !== undefined}
                      onCommit={(fieldName, value) => {
                        if (readOnlyFields?.[fieldName] !== undefined) return;
                        return fieldProps.onCommit(fieldName, value);
                      }}
                    />
                  )}
                  {reason !== undefined && <div className="settings-fields__readonly">{reason}</div>}
                </div>
              );
            })}
          </div>
        </details>
        </div>
      ))}
    </div>
  );
}
