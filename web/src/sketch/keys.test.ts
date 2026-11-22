import { describe, expect, it } from "vitest";
import { sketchKey, type KeyLike } from "./keys";
import { withUnit } from "./SketchTables";

const key = (k: string, mods: Partial<KeyLike> = {}): KeyLike => ({ key: k, ctrlKey: false, metaKey: false, shiftKey: false, altKey: false, ...mods });
const idle = { entryOpen: false, entryEmpty: true };
const typing = { entryOpen: true, entryEmpty: false };

describe("the sketch's keyboard (review A2, A3)", () => {
  it("undoes with Ctrl+Z and redoes with Ctrl+Shift+Z and Ctrl+Y", () => {
    expect(sketchKey(key("z", { ctrlKey: true }), idle)).toEqual({ kind: "undo" });
    expect(sketchKey(key("Z", { ctrlKey: true, shiftKey: true }), idle)).toEqual({ kind: "redo" });
    expect(sketchKey(key("y", { ctrlKey: true }), idle)).toEqual({ kind: "redo" });
    expect(sketchKey(key("z", { metaKey: true, shiftKey: true }), idle)).toEqual({ kind: "redo" });
    // Any other chord is kept from the app behind the sketch.
    expect(sketchKey(key("k", { ctrlKey: true }), idle)).toEqual({ kind: "swallow" });
  });

  it("picks tools by letter and keeps every other key from the app", () => {
    expect(sketchKey(key("l"), idle)).toEqual({ kind: "tool", tool: "line" });
    expect(sketchKey(key("R"), idle)).toEqual({ kind: "tool", tool: "rectangle" });
    expect(sketchKey(key("c"), idle)).toEqual({ kind: "tool", tool: "circle" });
    expect(sketchKey(key("a"), idle)).toEqual({ kind: "tool", tool: "arc" });
    expect(sketchKey(key("s"), idle)).toEqual({ kind: "tool", tool: "spline" });
    expect(sketchKey(key("p"), idle)).toEqual({ kind: "tool", tool: "polygon" });
    expect(sketchKey(key("v"), idle)).toEqual({ kind: "tool", tool: "select" });
    expect(sketchKey(key("2"), idle)).toEqual({ kind: "swallow" });
    expect(sketchKey(key("F5"), idle)).toEqual({ kind: "swallow" });
    expect(sketchKey(key("f"), idle)).toEqual({ kind: "fit" });
  });

  it("sends characters to a typed size while a draft takes one", () => {
    expect(sketchKey(key("4"), typing)).toEqual({ kind: "char", ch: "4" });
    // A letter is part of an expression such as W while a size is being typed.
    expect(sketchKey(key("W"), typing)).toEqual({ kind: "char", ch: "W" });
    expect(sketchKey(key("Tab", { shiftKey: true }), typing)).toEqual({ kind: "tab", back: true });
    expect(sketchKey(key("Backspace"), typing)).toEqual({ kind: "backspace" });
    expect(sketchKey(key("Backspace"), idle)).toEqual({ kind: "delete" });
    expect(sketchKey(key("Enter"), typing)).toEqual({ kind: "enter" });
    expect(sketchKey(key("Escape"), typing)).toEqual({ kind: "escape" });
  });
});

describe("one length unit (review A5)", () => {
  it("reads a bare dimension value as millimetres and keeps any other text", () => {
    expect(withUnit(" 5 ")).toBe("5 [mm]");
    expect(withUnit("30 [deg]")).toBe("30 [deg]");
    expect(withUnit("W * 2")).toBe("W * 2");
  });
});
