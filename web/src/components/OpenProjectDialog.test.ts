/**
 * The Open dialog's rules: which rows are openable, and what a row says when it is not.
 *
 * **Why this needs a test.** The dialog offers three different things - a project on disk, a folder
 * to walk into, and a sample that opens as a COPY - and getting the distinction wrong is silent: a
 * click either opens the wrong thing or does nothing. `project.browse` already marks the two kinds
 * (`kind: "project"` when the folder holds a `project.json`), so the rule here is only "believe the
 * server's mark and route accordingly".
 */
import { describe, expect, it } from "vitest";
import { previewUrl, shortPath, type RecentProject } from "./OpenProjectDialog";

describe("shortPath", () => {
  it("keeps the last components and elides the rest", () => {
    expect(shortPath("/a/b/c/duct.stokes")).toBe("…/c/duct.stokes");
    expect(shortPath("/a/duct.stokes")).toBe("/a/duct.stokes");
  });

  it("leaves a path it cannot shorten alone", () => {
    // A bare name is not a path, and prefixing an ellipsis to it would be a lie about depth.
    expect(shortPath("duct.stokes")).toBe("duct.stokes");
    expect(shortPath("")).toBe("");
  });
});

describe("a recent row", () => {
  it("carries the folder state the server computed", () => {
    // **`exists` comes from the server, per call.** A stored list goes stale the moment a folder is
    // moved, and a row that claims to open something it cannot is worse than one saying it is gone.
    const gone: RecentProject = { name: "duct", path: "/x/duct.stokes", exists: false };
    const here: RecentProject = { name: "pipe", path: "/x/pipe.stokes", exists: true };
    expect(gone.exists).toBe(false);
    expect(here.exists).toBe(true);
  });
});

describe("previewUrl", () => {
  it("encodes the project path as a query parameter", () => {
    const url = previewUrl("http://127.0.0.1:8740", "/a b/duct.stokes");
    expect(url).toBe("http://127.0.0.1:8740/api/project/preview?path=%2Fa%20b%2Fduct.stokes");
  });

  it("carries the session token, because an <img> cannot send a header", () => {
    // **Measured without it: HTTP 401**, and a blank row where the thumbnail should be. Every
    // `/api/*` route checks the token; the RPC socket puts it in its URL and the geometry fetches
    // pass it as a header, but an image element has only the query string.
    const url = previewUrl("http://x:1", "/p/d.stokes", "abc123");
    expect(url).toContain("token=abc123");
    expect(url).toContain("path=%2Fp%2Fd.stokes");
  });

  it("tolerates a trailing slash on the base", () => {
    expect(previewUrl("http://x:1/", "/p/d.stokes")).toBe("http://x:1/api/project/preview?path=%2Fp%2Fd.stokes");
  });

  it("returns nothing to fetch when it cannot build a URL", () => {
    // **"" means "show the placeholder"**, not "fetch the root path" - a row with no base must not
    // render a broken image, and an empty path must not become a request for the whole listing.
    expect(previewUrl("", "/p/d.stokes")).toBe("");
    expect(previewUrl("http://x:1", "")).toBe("");
  });
});
