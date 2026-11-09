/** The rules of the browser upload (T-219 A): the accept list, the name check, the words and the one-file rule. */
import { describe, expect, it } from "vitest";
import {
  acceptList,
  baseName,
  chooseFile,
  DEFAULT_MAX_UPLOAD_BYTES,
  importCancelledText,
  maxUploadBytes,
  sameProject,
  dragHasFiles,
  formatBytes,
  isSupportedName,
  progressFraction,
  progressText,
  readUploadReply,
  showImportHint,
  suffixOf,
  uploadErrorText,
  uploadHeaders,
  uploadUrl,
} from "./uploadModel";

const FORMATS = [
  { suffixes: [".step", ".stp"] },
  { suffixes: [".iges", ".igs"] },
  { suffixes: [".brep", ".brp"] },
  { suffixes: [".stl"] },
  { suffixes: [".obj"] },
  { suffixes: [".3mf"] },
];

describe("the accept list", () => {
  it("lists every suffix of every format once, in order", () => {
    expect(acceptList(FORMATS)).toBe(".step,.stp,.iges,.igs,.brep,.brp,.stl,.obj,.3mf");
    expect(acceptList([{ suffixes: [".STL"] }, { suffixes: [".stl"] }])).toBe(".stl");
  });
  it("is empty for no formats", () => {
    expect(acceptList([])).toBe("");
  });
});

describe("names", () => {
  it("reads the suffix in lower case, from the last segment only", () => {
    expect(suffixOf("Part.STEP")).toBe(".step");
    expect(suffixOf("C:\\parts.d\\bracket")).toBe("");
    expect(suffixOf(".hidden")).toBe("");
    expect(suffixOf("a.tar.stl")).toBe(".stl");
  });
  it("takes a supported name whatever its case", () => {
    expect(isSupportedName("duct.STL", FORMATS)).toBe(true);
    expect(isSupportedName("duct.pdf", FORMATS)).toBe(false);
    expect(isSupportedName("duct", FORMATS)).toBe(false);
  });
  it("sends the last segment as the name", () => {
    expect(baseName("C:\\parts\\duct.step")).toBe("duct.step");
    expect(baseName("a/b/c.stl")).toBe("c.stl");
  });
});

describe("progress", () => {
  it("formats bytes with one decimal below ten of a unit", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(812)).toBe("812 B");
    expect(formatBytes(1500)).toBe("1.5 kB");
    expect(formatBytes(12_400_000)).toBe("12 MB");
    expect(formatBytes(40_000_000)).toBe("40 MB");
    expect(formatBytes(2_500_000_000)).toBe("2.5 GB");
    expect(formatBytes(-5)).toBe("0 B");
  });
  it("says what is uploading and how far", () => {
    expect(progressText("part.step", 12_000_000, 40_000_000)).toBe("Uploading part.step 12 MB / 40 MB");
    expect(progressText("C:\\x\\part.step", 500, 0)).toBe("Uploading part.step 500 B");
  });
  it("is a fraction only when the total is known", () => {
    expect(progressFraction(10, 40)).toBe(0.25);
    expect(progressFraction(50, 40)).toBe(1);
    expect(progressFraction(10, 0)).toBeNull();
  });
});

describe("errors", () => {
  it("maps each status the contract names to a plain sentence", () => {
    expect(uploadErrorText(413, "big.step")).toContain("too large");
    expect(uploadErrorText(415, "x.pdf")).toContain("format is not supported");
    expect(uploadErrorText(401, "x.step")).toContain("session");
    expect(uploadErrorText(0, "x.step")).toContain("connection");
    expect(uploadErrorText(500, "x.step")).toContain("HTTP 500");
    expect(uploadErrorText(404, "x.step")).toContain("Server path");
  });
});

describe("the URL and the reply", () => {
  it("encodes the name, and the token is not in the URL but in the X-Stokes-Token header", () => {
    const token = "a b&c-secret";
    const url = uploadUrl("http://127.0.0.1:8765/", "my part.step");
    expect(url).toBe("http://127.0.0.1:8765/api/upload?name=my%20part.step");
    expect(url).not.toContain("token");
    expect(url).not.toContain("secret");
    expect(uploadHeaders(token)).toEqual({ "Content-Type": "application/octet-stream", "X-Stokes-Token": token });
  });
  it("reads a good reply and refuses the rest", () => {
    expect(readUploadReply('{"path":"/data/up/x.step","size":12,"sha256":"ab"}')).toEqual({ path: "/data/up/x.step", size: 12, sha256: "ab" });
    expect(readUploadReply('{"path":""}')).toBeNull();
    expect(readUploadReply("not json")).toBeNull();
    expect(readUploadReply("[1]")).toBeNull();
    expect(readUploadReply('{"size":1}')).toBeNull();
  });
});

describe("choosing the one file", () => {
  it("takes a single supported file", () => {
    const f = { name: "duct.stl", size: 10 };
    expect(chooseFile([f], FORMATS)).toEqual({ ok: true, file: f });
  });
  it("refuses several, none, an unknown suffix and an empty file, each with its reason", () => {
    const a = { name: "a.stl", size: 1 };
    const b = { name: "b.stl", size: 1 };
    expect(chooseFile([a, b], FORMATS)).toMatchObject({ ok: false, reason: expect.stringContaining("one file at a time") });
    expect(chooseFile([], FORMATS)).toMatchObject({ ok: false });
    expect(chooseFile([{ name: "notes.txt", size: 1 }], FORMATS)).toMatchObject({ ok: false, reason: expect.stringContaining("not a geometry file") });
    expect(chooseFile([{ name: "a.stl", size: 0 }], FORMATS)).toMatchObject({ ok: false, reason: expect.stringContaining("empty") });
  });
});

describe("the size limit", () => {
  const big = { name: "duct.step", size: 3_000_000 };
  it("reads max_upload_bytes from the formats reply and falls back to 1 GiB for a reply without a usable one", () => {
    expect(maxUploadBytes({ formats: [], max_upload_bytes: 5_000_000 })).toBe(5_000_000);
    expect(maxUploadBytes({ formats: [] })).toBe(DEFAULT_MAX_UPLOAD_BYTES);
    expect(maxUploadBytes({ max_upload_bytes: "lots" })).toBe(1024 ** 3);
    expect(maxUploadBytes({ max_upload_bytes: 0 })).toBe(1024 ** 3);
    expect(maxUploadBytes({ max_upload_bytes: -1 })).toBe(1024 ** 3);
    expect(maxUploadBytes(null)).toBe(1024 ** 3);
    expect(DEFAULT_MAX_UPLOAD_BYTES).toBe(1_073_741_824);
  });
  it("refuses a file over the limit before anything is sent, with both sizes in the message", () => {
    const refused = chooseFile([big], FORMATS, 2_000_000);
    expect(refused).toMatchObject({ ok: false, reason: expect.stringContaining("3 MB") });
    expect(refused).toMatchObject({ ok: false, reason: expect.stringContaining("up to 2 MB") });
    expect(chooseFile([big], FORMATS, 3_000_000)).toMatchObject({ ok: true });
  });
  it("uses 1 GiB when no limit is given", () => {
    expect(chooseFile([{ name: "a.stl", size: DEFAULT_MAX_UPLOAD_BYTES }], FORMATS)).toMatchObject({ ok: true });
    expect(chooseFile([{ name: "a.stl", size: DEFAULT_MAX_UPLOAD_BYTES + 1 }], FORMATS)).toMatchObject({ ok: false, reason: expect.stringContaining("takes files up to") });
  });
});

describe("an upload that outlives its project", () => {
  const a = { project: { name: "pipe" }, epoch: 0 };
  it("is the same project while nothing moved, and not after a switch, a new project of the same name, or a close", () => {
    expect(sameProject(a, { project: { name: "pipe" }, epoch: 0 })).toBe(true);
    expect(sameProject(a, { project: { name: "duct" }, epoch: 0 })).toBe(false);
    expect(sameProject(a, { project: { name: "pipe" }, epoch: 1 })).toBe(false);
    expect(sameProject(a, { project: null, epoch: 0 })).toBe(false);
    expect(sameProject({ project: null, epoch: 0 }, a)).toBe(false);
  });
});

describe("the import cancelled toast", () => {
  it("says Import cancelled, with the file when it is known", () => {
    expect(importCancelledText("C:\\parts\\duct.stl")).toBe("Import cancelled: duct.stl.");
    expect(importCancelledText(null)).toBe("Import cancelled.");
  });
});

describe("drags", () => {
  it("reacts to files and to nothing else", () => {
    expect(dragHasFiles(["text/plain", "Files"])).toBe(true);
    expect(dragHasFiles(["text/plain"])).toBe(false);
    expect(dragHasFiles(null)).toBe(false);
  });
});

describe("the empty-project hint", () => {
  const empty = { type: "root", children: [{ type: "group", children: [] }] };
  const withPart = { type: "root", children: [{ type: "group", children: [{ type: "part" }] }] };
  it("shows for an open project with no part and nothing coming", () => {
    expect(showImportHint({ hasProject: true, tree: empty, uploading: false, importing: false })).toBe(true);
  });
  it("is gone once a part exists, while an upload or import runs, and with no project", () => {
    expect(showImportHint({ hasProject: true, tree: withPart, uploading: false, importing: false })).toBe(false);
    expect(showImportHint({ hasProject: true, tree: empty, uploading: true, importing: false })).toBe(false);
    expect(showImportHint({ hasProject: true, tree: empty, uploading: false, importing: true })).toBe(false);
    expect(showImportHint({ hasProject: false, tree: empty, uploading: false, importing: false })).toBe(false);
    expect(showImportHint({ hasProject: true, tree: null, uploading: false, importing: false })).toBe(false);
  });
});
