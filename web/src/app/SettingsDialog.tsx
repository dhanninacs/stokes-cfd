/**
 * Settings (T-178): one modal dialog with a section list, opened from Project → Settings…, the
 * `app.settings` action (`Ctrl Shift ,`), the palette and the Appearance popover's "All
 * settings…". `settingsSections.ts` is the table it renders; this file is the controls.
 *
 * Where a value lives decides how it is written, and it is always read back from that store, never kept
 * here, so a change made from the console or by an agent shows:
 *  - the workspace document, through `useWorkspace.setValue` (the console and the journal see
 *    `workspace.set`). The user's preferences are written with `{ scope: "user" }` explicitly: they are
 *    never the project's, and the server refuses them in the project scope. `setValue` never rejects;
 *    it resolves `false` after `execute` has shown the server's message, and the control then says so;
 *  - the open project, through `project.set_units`, an undo step on that project;
 *  - this device (`app/prefs.ts`), for reduced motion.
 *
 * Export, Import, Save preset, Reset and the notices open over this dialog. They are siblings of it,
 * not children, so a key or press inside one never reaches this one through the React tree, and
 * `Dialog` lets only the top dialog take Escape.
 */
import { useEffect, useMemo, useState, type KeyboardEvent, type ReactNode } from "react";
import { Brush, ChevronsUpDown, Cpu, FolderTree, Download, FileText, Info, LayoutGrid, Monitor, Mouse, Moon, Palette, RotateCcw, Ruler, Save, Sun, Upload, type LucideIcon } from "lucide-react";
import { StokesApiError } from "../api/client";
import type { LicenseInfo, EngineInfo } from "../api/types";
import { Button, Segmented, Switch } from "../components/ui";
import { Dialog } from "../components/Dialog";
import { useStore } from "../state/store";
import ComputeSection from "./ComputeSection";
import FoldersSection from "./FoldersSection";
import HostsSection from "./HostsSection";
import { ResetDialog } from "../workspace/Customise";
import { useWorkspace } from "../workspace/store";
import { chooseTheme } from "../workspace/theme";
import { ExportDialog, ImportDialog, PresetRow, SavePresetDialog, choiceOf, themeName } from "../workspace/WorkspacePanel";
import { isCustomTheme, type MouseScheme } from "../workspace/types";
import { NAV_MODES } from "./actions";
import { REDUCED_MOTION_KEY, readPref, useBoolPref, useStringPref } from "./prefs";
import {
  SECTIONS,
  choiceDisabled,
  optionDisabled,
  optionShown,
  optionValue,
  sectionById,
  type Choice,
  type Option,
  type OptionState,
  type SectionIcon,
} from "./settingsSections";

const ICONS: Record<SectionIcon, LucideIcon> = { Palette, Ruler, Mouse, LayoutGrid, Info, Cpu, FolderTree };
const NOT_SAVED = "Not saved; the console has the reason";
const NOT_ON_SERVER = "not available on this server";

type Child = "save" | "export" | "import" | "reset" | "notices" | null;

export default function SettingsDialog() {
  const open = useWorkspace((s) => s.settingsOpen);
  const setOpen = useWorkspace((s) => s.setSettingsOpen);
  const [tabId, setTabId] = useStringPref("settings.tab", SECTIONS[0].id);
  const [child, setChild] = useState<Child>(null);
  const presets = useWorkspace((s) => s.presets);
  const hasProjectFile = useWorkspace((s) => s.files.project?.exists === true);
  const section = sectionById(tabId);

  useEffect(() => {
    if (!open) setChild(null);
  }, [open]);

  const close = () => setOpen(false);
  const closeChild = () => setChild(null);

  /** ArrowUp and ArrowDown move between the sections as they move in a segmented control; Home and End jump. */
  const onTabKeys = (e: KeyboardEvent<HTMLDivElement>) => {
    const at = SECTIONS.findIndex((s) => s.id === section.id);
    const next = e.key === "ArrowDown" ? at + 1 : e.key === "ArrowUp" ? at - 1 : e.key === "Home" ? 0 : e.key === "End" ? SECTIONS.length - 1 : null;
    if (next === null) return;
    e.preventDefault();
    const target = SECTIONS[(next + SECTIONS.length) % SECTIONS.length];
    setTabId(target.id);
    const list = e.currentTarget;
    requestAnimationFrame(() => list.querySelector<HTMLElement>("[aria-selected='true']")?.focus());
  };

  return (
    <>
      <Dialog
        open={open}
        title="Settings"
        size="settings"
        onClose={close}
        focus="[role='tab'][aria-selected='true']"
        actions={
          <button type="button" className="btn primary no-drag" onClick={close}>
            Done
          </button>
        }
      >
        <div className="settings-body">
          <div className="settings-nav" role="tablist" aria-orientation="vertical" aria-label="Settings sections" onKeyDown={onTabKeys}>
            {SECTIONS.map((s) => {
              const Icon = ICONS[s.icon];
              const selected = s.id === section.id;
              return (
                <button
                  key={s.id}
                  type="button"
                  role="tab"
                  id={`settings-tab-${s.id}`}
                  aria-selected={selected}
                  aria-controls="settings-page"
                  tabIndex={selected ? 0 : -1}
                  className="settings-tab no-drag"
                  onClick={() => setTabId(s.id)}
                >
                  <Icon size={14} aria-hidden="true" />
                  {s.title}
                </button>
              );
            })}
          </div>
          <div className="settings-page" role="tabpanel" id="settings-page" aria-labelledby={`settings-tab-${section.id}`}>
            <p className="muted text-[12px] mb-4" title="Configure the app here. For mesh, physics and solver settings, select an item in the simulation tree.">Settings for the app.</p>
            <Page sectionId={section.id} onChild={setChild} onClose={close} />
          </div>
        </div>
      </Dialog>
      <SavePresetDialog open={open && child === "save"} presets={presets} onClose={closeChild} />
      <ExportDialog open={open && child === "export"} onClose={closeChild} />
      <ImportDialog open={open && child === "import"} onClose={closeChild} />
      <ResetDialog open={open && child === "reset"} hasProject={hasProjectFile} onClose={closeChild} />
      <NoticesDialog open={open && child === "notices"} onClose={closeChild} />
    </>
  );
}

// ---------------------------------------------------------------------------------------------- what the controls read
/** Everything the table's pure functions read, taken from the stores the values live in. */
function useOptionState(): OptionState {
  const workspace = useWorkspace((s) => s.document);
  const projectFolder = useWorkspace((s) => s.files.project !== null);
  const project = useStore((s) => s.project);
  const [reduced] = useBoolPref(REDUCED_MOTION_KEY, false);
  return useMemo(
    () => ({
      workspace,
      project,
      projectFolder,
      device: (key: string) => (key === REDUCED_MOTION_KEY ? (reduced ? "1" : "0") : readPref(key)),
      theme: choiceOf,
    }),
    [workspace, project, projectFolder, reduced],
  );
}

function optionsOf(sectionId: string): readonly Option[] {
  return sectionById(sectionId).options;
}

/** The page of one section: its options in table order, an option that does not apply to the setting left out. */
function Page({ sectionId, onChild, onClose }: { sectionId: string; onChild: (child: Child) => void; onClose: () => void }) {
  const state = useOptionState();
  const [errors, setErrors] = useState<Record<string, string>>({});
  const note = (id: string, ok: boolean) => setErrors((was) => (ok ? (id in was ? Object.fromEntries(Object.entries(was).filter(([k]) => k !== id)) : was) : { ...was, [id]: NOT_SAVED }));
  useEffect(() => setErrors({}), [sectionId]);

  /** A preference or other workspace value. Preferences are the user's: the scope is named, never inferred. */
  const writeWorkspace = async (option: Option, value: unknown) => {
    if (option.store.kind !== "workspace") return;
    const preference = option.store.path[0] === "preferences";
    note(option.id, await useWorkspace.getState().setValue(option.store.path, value, preference ? { scope: "user" } : {}));
  };

  return (
    <>
      {optionsOf(sectionId)
        .filter((o) => optionShown(o, state))
        .map((o) => (
          <div key={o.id} className="popover-section settings-row" data-setting={o.id}>
            <OptionControl option={o} state={state} write={writeWorkspace} note={note} onChild={onChild} onClose={onClose} />
            {errors[o.id] && (
              <p className="settings-error" role="alert">
                {errors[o.id]}
              </p>
            )}
          </div>
        ))}
    </>
  );
}

interface ControlProps {
  option: Option;
  state: OptionState;
  write: (option: Option, value: unknown) => Promise<void>;
  note: (id: string, ok: boolean) => void;
  onChild: (child: Child) => void;
  onClose: () => void;
}

function Title({ option }: { option: Option }) {
  return <div className="section-title popover-title">{option.label}</div>;
}

function Help({ option }: { option: Option }) {
  return option.help ? <p className="settings-help">{option.help}</p> : null;
}

const APPEARANCE_ICONS: Record<string, ReactNode> = { dark: <Moon size={13} />, light: <Sun size={13} />, auto: <Monitor size={13} /> };

function OptionControl({ option, state, write, note, onChild, onClose }: ControlProps) {
  const { family, appearance } = choiceOf(state.workspace.theme);
  switch (option.id) {
    case "appearance.style":
      return (
        <>
          <Title option={option} />
          <Segmented label="Style" value={family} onChange={(next) => chooseTheme(themeName(next, appearance))} options={(option.choices ?? []).map((c) => ({ value: c.value as typeof family, label: c.label }))} />
        </>
      );
    case "appearance.appearance":
      return (
        <>
          <Title option={option} />
          <Segmented
            label="Appearance"
            value={appearance}
            onChange={(next) => chooseTheme(themeName(family, next))}
            options={(option.choices ?? []).map((c) => ({ value: c.value as typeof appearance, label: c.label, icon: APPEARANCE_ICONS[c.value] }))}
          />
        </>
      );
    case "appearance.custom": {
      const theme = state.workspace.theme;
      const custom = isCustomTheme(theme) ? theme : null;
      return (
        <div className="flex items-center justify-between gap-2 text-[12px]">
          <span className="muted min-w-0 truncate">{custom ? `Custom theme${custom.title ? ` “${custom.title}”` : ""}` : "No custom theme yet"}</span>
          <Button
            variant="ghost"
            className="!h-7 !px-2.5 text-[12px]"
            onClick={() => {
              onClose();
              useWorkspace.getState().setThemeEditorOpen(true);
            }}
          >
            <Brush size={13} /> Theme editor…
          </Button>
        </div>
      );
    }
    case "appearance.reducedMotion":
      return <ReducedMotion option={option} />;
    case "appearance.grid":
      return <Switch checked={optionValue(option, state) === true} onChange={(next) => void write(option, next)} label={option.label} description={option.help} />;
    /*
      **The tree's arrangement** (Codex's 063500). A presentation switch: `write` goes through
      `workspace.setValue`, so the choice is saved the way every other preference is, and switching leaves
      the model, the selection and every other preference alone.

      **`default` returns null**, so an option id with no case here renders NOTHING - measured: the entry
      existed in the section table and the dialog showed no control at all.
    */
    case "appearance.treeLayout":
      return (
        <>
          <Title option={option} />
          <SelectField
            label={option.label}
            value={String(optionValue(option, state))}
            choices={option.choices ?? []}
            disabled={optionDisabled(option, state)}
            onChange={(layout) => void write(option, layout)}
          />
          <Help option={option} />
        </>
      );
    case "units.system": {
      const reason = optionDisabled(option, state);
      return (
        <>
          <Title option={option} />
          <SelectField
            label={option.label}
            value={String(optionValue(option, state))}
            choices={option.choices ?? []}
            disabled={reason}
            onChange={(system) => {
              void useStore
                .getState()
                .execute("project.set_units", { system })
                .then(() => note(option.id, true))
                .catch(() => note(option.id, false));
            }}
          />
          {reason && <p className="settings-help">{reason}.</p>}
          <Help option={option} />
        </>
      );
    }
    case "units.importUnit":
      return (
        <>
          <Title option={option} />
          <SelectField label={option.label} value={String(optionValue(option, state))} choices={option.choices ?? []} disabled={optionDisabled(option, state)} onChange={(unit) => void write(option, unit)} />
          <Help option={option} />
        </>
      );
    case "mouse.scheme": {
      const current = String(optionValue(option, state));
      return (
        <>
          <Title option={option} />
          <RadioList
            label="Mouse scheme"
            value={current}
            choices={(option.choices ?? []).map((c) => ({ ...c, description: NAV_MODES.find((m) => m.id === c.value)?.summary }))}
            onChange={(scheme) => {
              // The viewport takes it at once; the workspace write is what makes it last.
              useStore.getState().setNavMode(scheme as MouseScheme);
              void write(option, scheme);
            }}
          />
          <Help option={option} />
        </>
      );
    }
    case "mouse.orbitLeft":
      return (
        <Switch
          checked={optionValue(option, state) === true}
          onChange={(next) => {
            useStore.getState().setOrbitMode(next);
            void write(option, next);
          }}
          label={option.label}
          description={option.help}
        />
      );
    case "workspace.scope":
      return <ScopeControl option={option} state={state} />;
    case "compute.hosts":
      return <ComputeSection />;
    case "compute.remoteHosts":
      return <HostsSection />;
    case "folders.paths":
      return <FoldersSection />;
    case "workspace.presets":
      return <PresetList option={option} />;
    case "workspace.manage":
      return (
        <div className="flex flex-wrap gap-1">
          <Button variant="ghost" className="ws-small" onClick={() => onChild("save")}>
            <Save size={13} /> Save current as…
          </Button>
          <Button variant="ghost" className="ws-small" onClick={() => onChild("export")}>
            <Download size={13} /> Export…
          </Button>
          <Button variant="ghost" className="ws-small" onClick={() => onChild("import")}>
            <Upload size={13} /> Import…
          </Button>
        </div>
      );
    case "workspace.reset":
      return (
        <>
          <div className="flex flex-wrap gap-1">
            <Button
              variant="ghost"
              className="ws-small"
              onClick={() => {
                onClose();
                useWorkspace.getState().setCustomising(true);
              }}
            >
              <LayoutGrid size={13} /> Customise…
            </Button>
            <Button variant="ghost" className="ws-small" onClick={() => onChild("reset")}>
              <RotateCcw size={13} /> Reset workspace…
            </Button>
          </div>
          <p className="settings-help" title="Reset workspace also resets your mouse scheme and default import unit.">Also resets mouse scheme and import unit.</p>
        </>
      );
    case "about.info":
      return <AboutInfo option={option} />;
    case "about.notices":
      return <NoticesButton option={option} onChild={onChild} />;
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------------------------- controls
function ReducedMotion({ option }: { option: Option }) {
  const [reduced, setReduced] = useBoolPref(REDUCED_MOTION_KEY, false);
  return <Switch checked={reduced} onChange={setReduced} label={option.label} description={option.help} />;
}

/** A drawn select: the chevron is part of the markup, a native arrow is never shown (DESIGN.md §2). */
function SelectField({ label, value, choices, disabled, onChange }: { label: string; value: string; choices: readonly Choice[]; disabled: string | null; onChange: (next: string) => void }) {
  const known = choices.some((c) => c.value === value);
  return (
    <div className="select-wrap">
      <select className="field" aria-label={label} value={value} disabled={disabled !== null} title={disabled ?? undefined} onChange={(e) => onChange(e.target.value)}>
        {!known && (
          <option value="" disabled hidden>
            –
          </option>
        )}
        {choices.map((c) => (
          <option key={c.value} value={c.value}>
            {c.label}
          </option>
        ))}
      </select>
      <ChevronsUpDown size={13} className="select-chevron" aria-hidden="true" />
    </div>
  );
}

/** Radio rows with a description line each; the arrow keys move the choice as they do in a segmented control. */
function RadioList({ label, value, choices, onChange }: { label: string; value: string; choices: readonly Choice[]; onChange: (next: string) => void }) {
  const move = (e: KeyboardEvent<HTMLDivElement>) => {
    const step = e.key === "ArrowDown" || e.key === "ArrowRight" ? 1 : e.key === "ArrowUp" || e.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    e.preventDefault();
    const at = Math.max(0, choices.findIndex((c) => c.value === value));
    const next = choices[(at + step + choices.length) % choices.length];
    onChange(next.value);
    const group = e.currentTarget;
    requestAnimationFrame(() => group.querySelector<HTMLElement>("[aria-checked='true']")?.focus());
  };
  return (
    <div role="radiogroup" aria-label={label} className="flex flex-col gap-0.5" onKeyDown={move}>
      {choices.map((c) => (
        <button
          key={c.value}
          type="button"
          role="radio"
          aria-checked={c.value === value}
          tabIndex={c.value === value ? 0 : -1}
          className="settings-choice no-drag"
          onClick={() => onChange(c.value)}
        >
          <span className="settings-dot" aria-hidden="true" />
          <span className="min-w-0">
            <span className="block text-[12.5px]">{c.label}</span>
            {c.description && <span className="muted block text-[11.5px] leading-snug">{c.description}</span>}
          </span>
        </button>
      ))}
    </div>
  );
}

function ScopeControl({ option, state }: { option: Option; state: OptionState }) {
  const scope = useWorkspace((s) => (s.scope === "project" && s.files.project ? "project" : "user"));
  const setScope = useWorkspace((s) => s.setScope);
  return (
    <>
      <Title option={option} />
      <Segmented
        label="Customise scope"
        value={scope}
        onChange={setScope}
        options={(option.choices ?? []).map((c) => ({ value: c.value as "user" | "project", label: c.label, disabled: choiceDisabled(c, state) ?? undefined }))}
      />
      <Help option={option} />
    </>
  );
}

function PresetList({ option }: { option: Option }) {
  const presets = useWorkspace((s) => s.presets);
  const loadPresets = useWorkspace((s) => s.loadPresets);
  const applyPreset = useWorkspace((s) => s.applyPreset);
  useEffect(() => {
    void loadPresets();
  }, [loadPresets]);
  const shipped = presets.filter((p) => p.source === "shipped");
  const saved = presets.filter((p) => p.source !== "shipped");
  return (
    <>
      <Title option={option} />
      <ul className="preset-list" aria-label="Layout presets">
        {shipped.map((p) => (
          <PresetRow key={p.name} preset={p} onApply={() => void applyPreset(p.name, p.title)} />
        ))}
        {saved.length > 0 && (
          <li className="preset-heading" aria-hidden="true">
            Saved
          </li>
        )}
        {saved.map((p) => (
          <PresetRow key={p.name} preset={p} onApply={() => void applyPreset(p.name, p.title)} />
        ))}
        {presets.length === 0 && <li className="muted px-1 py-1 text-[12px]">The presets load from the server.</li>}
      </ul>
    </>
  );
}

// ---------------------------------------------------------------------------------------------- About
/** What `app.info` returns, read defensively: it is data from a socket. */
interface AppInfo {
  version: string;
  python: string;
  platform: string;
  home: string;
}

function readAppInfo(raw: unknown): AppInfo | null {
  if (typeof raw !== "object" || raw === null) return null;
  const r = raw as Record<string, unknown>;
  const text = (v: unknown) => (typeof v === "string" ? v : "");
  return { version: text(r.version), python: text(r.python), platform: text(r.platform), home: text(r.home) };
}

type Loaded<T> = { state: "loading" } | { state: "missing" } | { state: "failed" } | { state: "ok"; value: T };

async function ask<T>(method: string, read: (raw: unknown) => T | null): Promise<Loaded<T>> {
  const client = useStore.getState().client;
  if (!client) return { state: "failed" };
  try {
    const value = read(await client.call(method));
    return value === null ? { state: "failed" } : { state: "ok", value };
  } catch (err) {
    return err instanceof StokesApiError && err.code === "api.unknown_command" ? { state: "missing" } : { state: "failed" };
  }
}

function describeLicence(licence: LicenseInfo | null): string {
  if (!licence) return NOT_ON_SERVER;
  if (licence.edition !== "licensed") return "Free edition";
  const who = licence.holder ? ` for ${licence.holder}` : "";
  const until = licence.expires ? `, expires ${licence.expires}` : "";
  return `Licensed${who}${until}`;
}

function describeEngine(engine: EngineInfo | null): string {
  if (!engine) return NOT_ON_SERVER;
  return engine.attached ? [engine.name ?? "Engine", engine.version].filter(Boolean).join(" ") : "No solver engine is attached yet";
}

function fileText(file: { path: string; exists: boolean } | null | undefined, none: string): string {
  if (!file) return none;
  return file.exists ? file.path : `${file.path} (not created yet)`;
}

function AboutInfo({ option }: { option: Option }) {
  const [info, setInfo] = useState<Loaded<AppInfo>>({ state: "loading" });
  const storeLicence = useStore((s) => s.licence);
  const storeEngine = useStore((s) => s.engine);
  const [licence, setLicence] = useState<LicenseInfo | null>(storeLicence);
  const [engine, setEngine] = useState<EngineInfo | null>(storeEngine);
  const files = useWorkspace((s) => s.files);

  // Fetched when the tab opens, so it shows the server as it is now.
  useEffect(() => {
    let live = true;
    void ask("app.info", readAppInfo).then((r) => live && setInfo(r));
    void ask<LicenseInfo>("license.info", (raw) => raw as LicenseInfo).then((r) => live && r.state === "ok" && setLicence(r.value));
    void ask<EngineInfo>("engine.info", (raw) => raw as EngineInfo).then((r) => live && r.state === "ok" && setEngine(r.value));
    return () => {
      live = false;
    };
  }, []);

  const from = (pick: (i: AppInfo) => string): string =>
    info.state === "ok" ? pick(info.value) || "unknown" : info.state === "loading" ? "…" : info.state === "missing" ? NOT_ON_SERVER : "could not be read";
  const rows: [string, string][] = [
    ["Version", from((i) => i.version)],
    ["Python", from((i) => i.python)],
    ["Platform", from((i) => i.platform)],
    ["Stokes home", from((i) => i.home)],
    ["Workspace file (user)", fileText(files.user, NOT_ON_SERVER)],
    ["Workspace file (project)", fileText(files.project, "no project folder")],
    ["Licence", describeLicence(licence)],
    ["Engine", describeEngine(engine)],
  ];
  return (
    <>
      <Title option={option} />
      <dl className="settings-facts" aria-label="About this installation">
        {rows.map(([name, value]) => (
          <div key={name} className="contents">
            <dt>{name}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}

/** The notices command exists on this server when its command list says so (an older server has no `app.notices`). */
function useHasNotices(): boolean {
  const commands = useStore((s) => s.commands);
  return commands.length === 0 || commands.some((c) => c.name === "app.notices");
}

function NoticesButton({ option, onChild }: { option: Option; onChild: (child: Child) => void }) {
  const available = useHasNotices();
  return (
    <>
      <Title option={option} />
      <Button variant="ghost" className="ws-small" disabled={!available} title={available ? undefined : "This server has no notices to show"} onClick={() => onChild("notices")}>
        <FileText size={13} /> Third-party notices…
      </Button>
      {!available && <p className="settings-help">This server has no notices to show ({NOT_ON_SERVER}).</p>}
    </>
  );
}

function NoticesDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [text, setText] = useState("");
  const [source, setSource] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    setText("");
    setSource("");
    setProblem(null);
    let live = true;
    void ask("app.notices", (raw) => {
      const r = raw as { text?: unknown; source?: unknown } | null;
      return r && typeof r.text === "string" ? { text: r.text, source: typeof r.source === "string" ? r.source : "" } : null;
    }).then((r) => {
      if (!live) return;
      if (r.state === "ok") {
        setText(r.value.text);
        setSource(r.value.source);
      } else setProblem(r.state === "missing" ? `The notices are ${NOT_ON_SERVER}.` : "The server could not read the notices; the console has the reason.");
    });
    return () => {
      live = false;
    };
  }, [open]);
  return (
    <Dialog
      open={open}
      title="Third-party notices"
      wide
      onClose={onClose}
      focus="textarea"
      actions={
        <button type="button" className="btn primary no-drag" onClick={onClose}>
          Done
        </button>
      }
    >
      <textarea className="field ws-json" readOnly value={problem ?? text} aria-label="Third-party notices" spellCheck={false} />
      {source && <p className="muted mt-1.5 text-[12px]">From the {source} copy.</p>}
    </Dialog>
  );
}
