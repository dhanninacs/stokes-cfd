import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StatusDot, Switch } from "./ui";

describe("StatusDot", () => {
  it("renders nothing when a row has no status", () => {
    expect(renderToStaticMarkup(createElement(StatusDot, { status: undefined }))).toBe("");
  });

  it("keeps the accessible wording for real statuses", () => {
    expect(renderToStaticMarkup(createElement(StatusDot, { status: "warning" }))).toContain('aria-label="Needs attention"');
    expect(renderToStaticMarkup(createElement(StatusDot, { status: "sideways" }))).toContain('aria-label="Status: sideways"');
  });
});

describe("Switch", () => {
  it("renders a disabled native switch that cannot receive pointer or keyboard activation", () => {
    const markup = renderToStaticMarkup(createElement(Switch, { checked: true, onChange: () => {}, label: "Locked", disabled: true }));
    expect(markup).toContain('role="switch"');
    expect(markup).toContain('aria-checked="true"');
    expect(markup).toContain("disabled=");
  });

  it("keeps enabled switches interactive", () => {
    const markup = renderToStaticMarkup(createElement(Switch, { checked: false, onChange: () => {}, label: "Enabled" }));
    expect(markup).toContain('aria-checked="false"');
    expect(markup).not.toContain("disabled=");
  });
});
