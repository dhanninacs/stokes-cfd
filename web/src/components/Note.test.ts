import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { folderPurpose } from "../panels/folderPurpose";
import { Note } from "./Note";

describe("Note: a few words, the sentence in the tooltip", () => {
  it("puts the sentence in title and aria-description, and the few words on the page", () => {
    const html = renderToStaticMarkup(createElement(Note, { tip: "Pick an item in the tree or a patch in the viewport." }, "Nothing is selected"));
    expect(html).toContain(">Nothing is selected</p>");
    expect(html).toContain('title="Pick an item in the tree or a patch in the viewport."');
    expect(html).toContain('aria-description="Pick an item in the tree or a patch in the viewport."');
  });

  it("is a plain line with no tooltip attributes when it has no tip", () => {
    const html = renderToStaticMarkup(createElement(Note, null, "Current cell size: 4 mm"));
    expect(html).not.toContain("title=");
    expect(html).not.toContain("aria-description");
  });

  it("carries a class and a data-ws hook from its caller", () => {
    const html = renderToStaticMarkup(createElement(Note, { className: "mesh-hint", "data-ws": "arrows-need-vector" }, "Needs the velocity field"));
    expect(html).toContain("mesh-hint");
    expect(html).toContain('data-ws="arrows-need-vector"');
  });
});

describe("a folder page's purpose line", () => {
  it("shows a few words and keeps the sentence for the tooltip", () => {
    const purpose = folderPurpose("/geometry", 3);
    expect(purpose.words).toBe("Bodies, faces and operations");
    expect(purpose.tip).toContain("Everything that makes the shape");
    expect(purpose.words.split(" ").length).toBeLessThanOrEqual(8);
  });

  it("falls back to the count, which needs no tooltip", () => {
    expect(folderPurpose("/somewhere/else", 1)).toEqual({ words: "1 item inside", tip: undefined });
    expect(folderPurpose("/somewhere/else", 4).words).toBe("4 items inside");
  });
});
