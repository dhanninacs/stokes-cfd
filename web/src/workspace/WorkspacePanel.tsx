/**
 * Appearance (the View island's popover, `Ctrl ,`): Style and Appearance, which write `theme`
 * to the workspace; the Layout presets (the five shipped ones and the user's), Save current as…,
 * Export… and Import…; the way into Customise mode and the theme editor; reduced motion, a per-device
 * preference. Also the theme editor itself, a card beside the viewport with a live preview.
 *
 * Export and import move JSON text: Export shows the document with Copy, and Download in a browser or
 * Save as… in the hosted window (the native dialog and the host's file bridge); Import takes pasted
 * text or a file, from the system's file chooser or, hosted, the native dialog and the bridge. Every
 * write goes through the workspace store, so the console and the journal see it.
 */
import { useEffect, useMemo, useRef, useState, type ChangeEvent, type RefObject } from "react";
import { Brush, Check, Copy, Download, LayoutGrid, Monitor, Moon, RotateCcw, Save, Settings2, Sun, Upload, X } from "lucide-react";
import { basename } from "../state/parts";
import { composeTheme, themeAppearance, themeFamily, useStore, type Theme, type ThemeFamily } from "../state/store";
import { Button, IconButton, Segmented, Switch } from "../components/ui";
import { Dialog } from "../components/Dialog";
import { Popover } from "../components/Menu";
import { Tooltip } from "../components/Tooltip";
import { REDUCED_MOTION_KEY, useBoolPref } from "../app/prefs";
import { host } from "../app/host";
import { displayAnchorButton } from "./displayAnchor";
import { useShortcut } from "./keymap";
import { useWorkspace } from "./store";
import { chooseTheme } from "./theme";
import { EDITOR_TOKENS, accentFamily, hex6, parseColour, toHex, toRgba, tokenProblem } from "./tokens";
import { isCustomTheme, type BaseThemeName, type CustomTheme, type PresetSummary, type ThemeSpec } from "./types";

export type AppearanceChoice = "dark" | "light" | "auto";

export function themeName(family: ThemeFamily, appearance: AppearanceChoice): ThemeSpec {
  if (appearance === "auto") return { name: family === "cupertino" ? "cupertino-auto" : family === "ubuntu" ? "ubuntu-auto" : "auto" };
  return { name: composeTheme(family, appearance) };
}

/** The Style and Appearance the document's theme stands for (a custom theme: its base's). */
export function choiceOf(spec: ThemeSpec): { family: ThemeFamily; appearance: AppearanceChoice } {
  if (isCustomTheme(spec)) return { family: themeFamily(spec.base), appearance: themeAppearance(spec.base) };
  if (spec.name === "auto") return { family: "stokes", appearance: "auto" };
  if (spec.name === "cupertino-auto") return { family: "cupertino", appearance: "auto" };
  if (spec.name === "ubuntu-auto") return { family: "ubuntu", appearance: "auto" };
  return { family: themeFamily(spec.name), appearance: themeAppearance(spec.name) };
}

const MAX_IMPORT_BYTES = 256 * 1024;
/** Name filter of the native file dialogs (Qt syntax). */
const JSON_FILTER = "JSON files (*.json);;All files (*)";
const PRESET_NAME = /^[a-z0-9_-]{1,64}$/;

// ---------------------------------------------------------------------------------------------- the popover
export function DisplaySettings() {
  const open = useWorkspace((s) => s.displayOpen);
  const setOpen = useWorkspace((s) => s.setDisplayOpen);
  const docTheme = useWorkspace((s) => s.document.theme);
  const presets = useWorkspace((s) => s.presets);
  const loadPresets = useWorkspace((s) => s.loadPresets);
  const applyPreset = useWorkspace((s) => s.applyPreset);
  const setCustomising = useWorkspace((s) => s.setCustomising);
  const setThemeEditorOpen = useWorkspace((s) => s.setThemeEditorOpen);
  const setSettingsOpen = useWorkspace((s) => s.setSettingsOpen);
  const [reduced, setReduced] = useBoolPref(REDUCED_MOTION_KEY, false);
  const [dialog, setDialog] = useState<"save" | "export" | "import" | null>(null);
  const customiseKeys = useShortcut("workspace.customise");
  const fallback = useRef<HTMLSpanElement>(null);
  // The popover hangs from the View island's button, or from a point at the bottom right when that
  // island is hidden (Ctrl , and the palette still open it).
  const anchor = useMemo(
    () =>
      ({
        get current() {
          return displayAnchorButton() ?? fallback.current;
        },
      }) as RefObject<HTMLElement | null>,
    [],
  );

  useEffect(() => {
    if (open) void loadPresets();
  }, [open, loadPresets]);

  const { family, appearance } = choiceOf(docTheme);
  const custom = isCustomTheme(docTheme) ? docTheme : null;
  const shipped = presets.filter((p) => p.source === "shipped");
  const saved = presets.filter((p) => p.source !== "shipped");

  return (
    <>
      <span ref={fallback} className="display-anchor" aria-hidden="true" />
      <Popover
        open={open}
        anchor={anchor}
        onClose={() => setOpen(false)}
        placement="top-end"
        role="dialog"
        label="Appearance"
        className="display-settings w-[304px]"
        focusSelector="[role='radio'][aria-checked='true']"
      >
        <div className="popover-section">
          <div className="section-title popover-title">Style</div>
          <Segmented
            label="Style"
            value={family}
            onChange={(next) => chooseTheme(themeName(next, appearance))}
            options={[
              { value: "stokes", label: "Stokes" },
              { value: "cupertino", label: "Cupertino" },
              { value: "ubuntu", label: "Ubuntu" },
            ]}
          />
        </div>
        <div className="popover-section">
          <div className="section-title popover-title">Appearance</div>
          <Segmented
            label="Appearance"
            value={appearance}
            onChange={(next) => chooseTheme(themeName(family, next))}
            options={[
              { value: "dark", label: "Dark", icon: <Moon size={13} /> },
              { value: "light", label: "Light", icon: <Sun size={13} /> },
              { value: "auto", label: "Auto", icon: <Monitor size={13} /> },
            ]}
          />
          {custom && (
            <div className="mt-2 flex items-center justify-between gap-2 text-[12px]">
              <span className="muted min-w-0 truncate">Custom theme{custom.title ? ` “${custom.title}”` : ""}</span>
              <Button variant="ghost" className="!h-7 !px-2.5 text-[12px]" onClick={() => setThemeEditorOpen(true)}>
                Edit…
              </Button>
            </div>
          )}
        </div>
        <div className="popover-section">
          <div className="section-title popover-title">Layout</div>
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
          <div className="mt-2 flex flex-wrap gap-1">
            <Button variant="ghost" className="ws-small" onClick={() => setDialog("save")}>
              <Save size={13} /> Save current as…
            </Button>
            <Button variant="ghost" className="ws-small" onClick={() => setDialog("export")}>
              <Download size={13} /> Export…
            </Button>
            <Button variant="ghost" className="ws-small" onClick={() => setDialog("import")}>
              <Upload size={13} /> Import…
            </Button>
          </div>
        </div>
        <div className="popover-section flex flex-col gap-1">
          <Tooltip label="Move panels, islands and top-bar items" hint={customiseKeys.hint} side="left" describe={false}>
            <Button
              variant="ghost"
              className="ws-wide"
              aria-keyshortcuts={customiseKeys.keys}
              onClick={() => {
                setOpen(false);
                setCustomising(true);
              }}
            >
              <LayoutGrid size={14} /> Customise…
            </Button>
          </Tooltip>
          <Button
            variant="ghost"
            className="ws-wide"
            onClick={() => {
              setOpen(false);
              setThemeEditorOpen(true);
            }}
          >
            <Brush size={14} /> Theme editor…
          </Button>
        </div>
        <div className="popover-section">
          <Switch checked={reduced} onChange={setReduced} label="Reduced motion" description="Fewer animations. The system setting always applies." />
        </div>
        <div className="popover-section">
          <Button
            variant="ghost"
            className="ws-wide"
            onClick={() => {
              setOpen(false);
              setSettingsOpen(true);
            }}
          >
            <Settings2 size={14} /> All settings…
          </Button>
        </div>
      </Popover>
      <SavePresetDialog open={dialog === "save"} presets={presets} onClose={() => setDialog(null)} />
      <ExportDialog open={dialog === "export"} onClose={() => setDialog(null)} />
      <ImportDialog open={dialog === "import"} onClose={() => setDialog(null)} />
    </>
  );
}

export function PresetRow({ preset, onApply }: { preset: PresetSummary; onApply: () => void }) {
  return (
    <li>
      <Tooltip label={preset.description || preset.title} side="left" describe={false}>
        <button type="button" className="preset-row no-drag" aria-label={`Apply the ${preset.title} layout`} onClick={onApply}>
          <span className="preset-title">{preset.title}</span>
          <span className="preset-description">{preset.description}</span>
        </button>
      </Tooltip>
    </li>
  );
}

// ---------------------------------------------------------------------------------------------- dialogs
export function SavePresetDialog({ open, presets, onClose }: { open: boolean; presets: PresetSummary[]; onClose: () => void }) {
  const savePreset = useWorkspace((s) => s.savePreset);
  const [name, setName] = useState("");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    if (!open) return;
    setName("");
    setTitle("");
    setDescription("");
    setError(null);
  }, [open]);
  const shipped = new Set(presets.filter((p) => p.source === "shipped").map((p) => p.name));
  const existing = presets.some((p) => p.source !== "shipped" && p.name === name);
  const submit = async () => {
    const n = name.trim();
    if (!PRESET_NAME.test(n)) return setError("Use 1 to 64 lower-case letters, digits, _ or -.");
    if (shipped.has(n)) return setError(`${n} is the name of a shipped preset.`);
    if (title.length > 64 || /[\r\n]/.test(title)) return setError("The title is one line of at most 64 characters.");
    if (await savePreset(n, title.trim() || undefined, description.trim() || undefined)) onClose();
  };
  return (
    <Dialog
      open={open}
      title="Save the layout as a preset"
      onClose={onClose}
      actions={
        <>
          <button type="button" className="btn no-drag" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary no-drag" onClick={() => void submit()}>
            Save
          </button>
        </>
      }
    >
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        className="flex flex-col gap-2"
      >
        <label className="text-[12.5px]">
          Name
          <input className={`field mt-1 ${error ? "error" : ""}`} value={name} placeholder="e.g. my_layout" spellCheck={false} autoComplete="off" onChange={(e) => (setName(e.target.value), setError(null))} />
        </label>
        <label className="text-[12.5px]">
          Title <span className="muted">(optional)</span>
          <input className="field mt-1" value={title} maxLength={64} placeholder="My layout" onChange={(e) => setTitle(e.target.value)} />
        </label>
        <label className="text-[12.5px]">
          Description <span className="muted">(optional, one line)</span>
          <input className="field mt-1" value={description} maxLength={240} onChange={(e) => setDescription(e.target.value)} />
        </label>
        <div className="min-h-[18px] text-[12px]" role={error ? "alert" : undefined} style={{ color: error ? "var(--danger)" : "var(--text-muted)" }}>
          {error ?? (existing ? `Replaces your preset ${name}.` : "Saves the whole workspace: arrangement, theme, shortcuts and viewport.")}
        </div>
        <button type="submit" hidden />
      </form>
    </Dialog>
  );
}

export async function copyText(text: string, area: HTMLTextAreaElement | null): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    if (!area) return false;
    area.select();
    return document.execCommand("copy");
  }
}

export function ExportDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const exportText = useWorkspace((s) => s.exportText);
  const [what, setWhat] = useState<"all" | "theme">("all");
  const [text, setText] = useState("");
  const [copied, setCopied] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  useEffect(() => {
    if (!open) return;
    setCopied(false);
    let live = true;
    void exportText("effective", what === "theme" ? ["theme"] : undefined).then((t) => live && setText(t ?? ""));
    return () => {
      live = false;
    };
  }, [open, what, exportText]);
  const fileName = what === "theme" ? "stokes-theme.json" : "stokes-workspace.json";
  const download = () => {
    const url = URL.createObjectURL(new Blob([text], { type: "application/json" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = fileName;
    a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };
  /** Hosted: the native save dialog, then the host writes the file (only to the path the user chose). */
  const saveAs = async () => {
    const path = await host.pickSaveFile("Export the workspace", fileName, JSON_FILTER);
    if (!path) return;
    const ok = await host.writeTextFile(path, text);
    useStore.getState().pushToast(ok ? "ok" : "error", ok ? `Saved ${basename(path)}.` : `Could not write ${basename(path)}.`);
  };
  return (
    <Dialog
      open={open}
      title="Export the workspace"
      wide
      onClose={onClose}
      focus="textarea"
      actions={
        <>
          {host.canSaveFiles ? (
            <button type="button" className="btn no-drag" onClick={() => void saveAs()} disabled={!text}>
              <Download size={13} /> Save as…
            </button>
          ) : (
            !host.available && (
              <button type="button" className="btn no-drag" onClick={download} disabled={!text}>
                <Download size={13} /> Download
              </button>
            )
          )}
          <button type="button" className="btn no-drag" disabled={!text} onClick={() => void copyText(text, area.current).then(setCopied)}>
            {copied ? <Check size={13} /> : <Copy size={13} />} {copied ? "Copied" : "Copy"}
          </button>
          <button type="button" className="btn primary no-drag" onClick={onClose}>
            Done
          </button>
        </>
      }
    >
      <Segmented
        label="What to export"
        value={what}
        onChange={setWhat}
        options={[
          { value: "all", label: "Whole workspace" },
          { value: "theme", label: "Theme only" },
        ]}
      />
      <textarea ref={area} className="field ws-json mt-2" readOnly value={text} aria-label="Workspace JSON" spellCheck={false} />
      <p className="muted mt-1.5 text-[12px]">Import it on another machine with Appearance → Import…, or share it as a file.</p>
    </Dialog>
  );
}

export function ImportDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const importText = useWorkspace((s) => s.importText);
  const scope = useWorkspace((s) => (s.scope === "project" && s.files.project ? "project" : "user"));
  const [text, setText] = useState("");
  const [note, setNote] = useState<{ error: boolean; text: string } | null>(null);
  const file = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (!open) return;
    setText("");
    setNote(null);
  }, [open]);
  const pick = async (e: ChangeEvent<HTMLInputElement>) => {
    const chosen = e.target.files?.[0];
    e.target.value = "";
    if (!chosen) return;
    if (chosen.size > MAX_IMPORT_BYTES) return setNote({ error: true, text: "The file is larger than 256 KiB." });
    setText(await chosen.text());
    setNote({ error: false, text: `Read ${chosen.name}.` });
  };
  /** Hosted: the native open dialog, then the host reads the file (only the one the user chose). In a browser: the file input. */
  const choose = async () => {
    if (!(host.canPickFiles && host.canSaveFiles)) return file.current?.click();
    const path = await host.pickFile("Import a workspace", "", JSON_FILTER);
    if (!path) return;
    const read = await host.readTextFile(path);
    if (read === null) return setNote({ error: true, text: `${basename(path)} could not be read as UTF-8 text.` });
    if (new TextEncoder().encode(read).length > MAX_IMPORT_BYTES) return setNote({ error: true, text: "The file is larger than 256 KiB." });
    setText(read);
    setNote({ error: false, text: `Read ${basename(path)}.` });
  };
  const submit = async () => {
    if (!text.trim()) return setNote({ error: true, text: "Paste a workspace, preset or theme file first." });
    try {
      JSON.parse(text);
    } catch {
      return setNote({ error: true, text: "This is not JSON." });
    }
    const result = await importText(text, scope);
    if (!result) return setNote({ error: true, text: "The server refused it; the console says why." });
    const dropped = result.warnings.filter((w) => w.kind === "invalid");
    useStore.getState().pushToast("ok", `Imported ${result.sections.join(", ")}${dropped.length ? ` (${dropped.length} value${dropped.length === 1 ? "" : "s"} left out)` : ""}.`);
    onClose();
  };
  return (
    <Dialog
      open={open}
      title="Import a workspace"
      wide
      onClose={onClose}
      focus="textarea"
      actions={
        <>
          <button type="button" className="btn no-drag" onClick={() => void choose()}>
            <Upload size={13} /> Choose file…
          </button>
          <button type="button" className="btn no-drag" onClick={onClose}>
            Cancel
          </button>
          <button type="button" className="btn primary no-drag" onClick={() => void submit()}>
            Import
          </button>
        </>
      }
    >
      <input ref={file} type="file" accept=".json,application/json" hidden onChange={(e) => void pick(e)} />
      <textarea className="field ws-json" value={text} onChange={(e) => (setText(e.target.value), setNote(null))} placeholder='{"version": 1, "layout": {...}}' aria-label="Workspace JSON to import" spellCheck={false} />
      <p className="mt-1.5 text-[12px]" role={note?.error ? "alert" : undefined} style={{ color: note?.error ? "var(--danger)" : "var(--text-muted)" }}>
        {note?.text ?? `Each section in the text replaces that section ${scope === "project" ? "for this project" : "for all projects"}. Exports, preset files and theme files work.`}
      </p>
    </Dialog>
  );
}

// ---------------------------------------------------------------------------------------------- theme editor
const COLOUR_ROWS: { token: string; label: string }[] = [
  { token: "--accent", label: "Accent" },
  { token: "--bg", label: "Background" },
  { token: "--panel", label: "Panels" },
  { token: "--text", label: "Text" },
  { token: "--text-muted", label: "Secondary text" },
  { token: "--vp-top", label: "Viewport top" },
  { token: "--vp-bottom", label: "Viewport bottom" },
  { token: "--vp-grid-minor", label: "Grid, minor" },
  { token: "--vp-grid-major", label: "Grid, major" },
];

function readToken(name: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function baseOf(spec: ThemeSpec, shown: Theme): BaseThemeName {
  return isCustomTheme(spec) ? spec.base : shown;
}

/**
 * The theme editor: a base (Style and Appearance), the main colours, the corner radius, and a title.
 * Every change previews at once (the draft is applied instead of the saved theme); Save writes the
 * whole theme with one `workspace.set path=theme`, after the values passed the rules of
 * theme-tokens.md. Changing the accent derives its family (stronger, soft, ink, fill, glow, text on
 * accent), so the chrome stays consistent; changing the panel colour sets the opaque panel too.
 */
export function ThemeEditor() {
  const open = useWorkspace((s) => s.themeEditorOpen);
  return open ? <Editor /> : null;
}

function Editor() {
  const docTheme = useWorkspace((s) => s.document.theme);
  const setDraft = useWorkspace((s) => s.setThemeDraft);
  const setOpen = useWorkspace((s) => s.setThemeEditorOpen);
  const shown = useStore((s) => s.theme);
  const [draft, setLocal] = useState<CustomTheme>(() =>
    isCustomTheme(docTheme) ? { ...docTheme, tokens: { ...docTheme.tokens } } : { base: baseOf(docTheme, shown), tokens: {} },
  );
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [computed, setComputed] = useState<Record<string, string>>({});
  const [message, setMessage] = useState<string | null>(null);

  // Preview: the draft is the theme while the editor is open.
  useEffect(() => setDraft(draft), [draft, setDraft]);
  // What the base shows for the tokens the draft leaves alone, read once the base is in place.
  useEffect(() => {
    const id = requestAnimationFrame(() => setComputed(Object.fromEntries(Object.keys(EDITOR_TOKENS).map((t) => [t, readToken(t)]))));
    return () => cancelAnimationFrame(id);
  }, [draft, shown]);

  const light = themeAppearance(draft.base) === "light";
  const cupertino = draft.base.startsWith("cupertino");
  const ubuntu = draft.base.startsWith("ubuntu");
  const value = (token: string) => texts[token] ?? draft.tokens[token] ?? computed[token] ?? "";

  const update = (patch: Record<string, string>, remove: string[] = []) =>
    setLocal((d) => {
      const tokens = { ...d.tokens, ...patch };
      for (const name of remove) delete tokens[name];
      return { ...d, tokens };
    });

  const setColour = (token: string, text: string) => {
    setTexts((t) => ({ ...t, [token]: text }));
    const problem = tokenProblem(token, text);
    setErrors((e) => {
      const { [token]: _old, ...rest } = e;
      void _old;
      return problem ? { ...rest, [token]: problem } : rest;
    });
    if (problem) return;
    const clean = EDITOR_TOKENS[token] === "hex" ? hex6(text) : text.trim();
    if (token === "--accent") update(accentFamily(clean, light, cupertino, ubuntu));
    else if (token === "--panel") {
      const c = parseColour(clean);
      update(c ? { "--panel": clean, "--panel-solid": toHex(c) } : { "--panel": clean });
    } else update({ [token]: clean });
  };

  const pickColour = (token: string, hex: string) => {
    // The picker knows #rrggbb only: a translucent token keeps its alpha.
    const before = parseColour(value(token));
    const picked = parseColour(hex);
    const next = picked && before && before.a < 1 && EDITOR_TOKENS[token] !== "hex" ? toRgba({ ...picked, a: before.a }) : hex;
    setTexts((t) => {
      const { [token]: _old, ...rest } = t;
      void _old;
      return rest;
    });
    setColour(token, next);
  };

  const radius = parseFloat(draft.tokens["--radius"] ?? computed["--radius"] ?? "12") || 0;
  const setRadius = (px: number) => update({ "--radius": `${px}px`, "--radius-sm": `${Math.round((px * 2) / 3)}px` });

  const setBase = (base: BaseThemeName) => {
    setTexts({});
    setErrors({});
    // Overrides are written for one appearance: a new appearance starts from the base's own values.
    setLocal((d) => (themeAppearance(d.base) === themeAppearance(base) ? { ...d, base } : { ...d, base, tokens: {} }));
  };

  const save = () => {
    const problems = Object.entries(draft.tokens)
      .map(([name, v]) => [name, tokenProblem(name, v)] as const)
      .filter((entry): entry is readonly [string, string] => entry[1] !== null);
    if (problems.length || Object.keys(errors).length) {
      setMessage(problems[0] ? `${problems[0][0]}: ${problems[0][1]}` : "Fix the fields marked in red first.");
      return;
    }
    if ((draft.title ?? "").length > 64 || /[\r\n]/.test(draft.title ?? "")) {
      setMessage("The title is one line of at most 64 characters.");
      return;
    }
    const theme: CustomTheme = { base: draft.base, tokens: draft.tokens };
    if (draft.title?.trim()) theme.title = draft.title.trim();
    chooseTheme(theme);
    setOpen(false);
  };

  const exportDraft = async () => {
    const text = `${JSON.stringify({ version: 1, theme: { base: draft.base, ...(draft.title ? { title: draft.title } : {}), tokens: draft.tokens } }, null, 2)}\n`;
    setMessage((await copyText(text, null)) ? "The theme JSON is on the clipboard." : "The clipboard is not available here; use Export… in Appearance after saving.");
  };

  return (
    <section className="theme-editor glass" role="dialog" aria-label="Theme editor">
      <header className="theme-editor-head">
        <Brush size={14} className="muted" aria-hidden="true" />
        <span className="section-title">Theme editor</span>
        <IconButton label="Close the theme editor" className="ml-auto !h-6 !w-6" onClick={() => setOpen(false)}>
          <X size={13} />
        </IconButton>
      </header>
      <div className="theme-editor-body">
        <div className="te-row">
          <span className="te-label">Style</span>
          <Segmented
            label="Base style"
            value={themeFamily(draft.base)}
            onChange={(f) => setBase(composeTheme(f, themeAppearance(draft.base)))}
            options={[
              { value: "stokes", label: "Stokes" },
              { value: "cupertino", label: "Cupertino" },
              { value: "ubuntu", label: "Ubuntu" },
            ]}
          />
        </div>
        <div className="te-row">
          <span className="te-label">Appearance</span>
          <Segmented
            label="Base appearance"
            value={themeAppearance(draft.base)}
            onChange={(a) => setBase(composeTheme(themeFamily(draft.base), a))}
            options={[
              { value: "dark", label: "Dark", icon: <Moon size={13} /> },
              { value: "light", label: "Light", icon: <Sun size={13} /> },
            ]}
          />
        </div>
        <label className="te-row">
          <span className="te-label">Title</span>
          <input className="field !h-8 text-[12.5px]" value={draft.title ?? ""} maxLength={64} placeholder="Untitled theme" onChange={(e) => setLocal((d) => ({ ...d, title: e.target.value }))} />
        </label>
        <div className="section-title mt-3 mb-1">Colours</div>
        {COLOUR_ROWS.map((row) => {
          const text = value(row.token);
          const parsed = parseColour(text);
          const error = errors[row.token];
          return (
            <div key={row.token} className="te-row">
              <span className="te-label">{row.label}</span>
              <span className="te-colour">
                <label className="swatch" style={{ background: parsed ? text : "transparent" }} title={`Pick ${row.label.toLowerCase()}`}>
                  <input type="color" className="sr-only" value={parsed ? toHex(parsed) : "#000000"} aria-label={`Pick ${row.label.toLowerCase()}`} onChange={(e) => pickColour(row.token, e.target.value)} />
                </label>
                <input
                  className={`field mono !h-8 ${error ? "error" : ""}`}
                  value={text}
                  placeholder="viewport default"
                  aria-label={`${row.label} (${row.token})`}
                  aria-invalid={error ? true : undefined}
                  title={error ?? row.token}
                  spellCheck={false}
                  onChange={(e) => setColour(row.token, e.target.value)}
                />
              </span>
            </div>
          );
        })}
        <div className="section-title mt-3 mb-1">Shape</div>
        <label className="te-row">
          <span className="te-label">Corner radius</span>
          <span className="te-slider">
            <input type="range" min={0} max={24} step={1} value={radius} aria-label="Corner radius" onChange={(e) => setRadius(Number(e.target.value))} />
            <span className="muted w-10 text-right text-[12px]">{radius}px</span>
          </span>
        </label>
        {message && (
          <p className="mt-2 text-[12px]" role="status" style={{ color: "var(--text-muted)" }}>
            {message}
          </p>
        )}
      </div>
      <footer className="theme-editor-foot">
        <Tooltip label="Back to the base's own values" side="top" describe={false}>
          <Button
            variant="ghost"
            className="ws-small"
            onClick={() => {
              setTexts({});
              setErrors({});
              setLocal((d) => ({ ...d, tokens: {} }));
            }}
          >
            <RotateCcw size={13} /> Reset
          </Button>
        </Tooltip>
        <IconButton label="Copy the theme as JSON" className="!h-7 !w-7" onClick={() => void exportDraft()}>
          <Copy size={13} />
        </IconButton>
        <Button variant="primary" className="ml-auto !h-7 text-[12.5px]" onClick={save}>
          Save as custom theme
        </Button>
      </footer>
    </section>
  );
}
