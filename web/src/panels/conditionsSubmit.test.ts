/** What the value panel sends (T-219 C): only the parameters a kind uses with the values it holds. */
import { describe, expect, it } from "vitest";
import type { ConditionKind } from "../api/types";
import { defaultValues, submittedValues } from "./conditions";

const VELOCITY: ConditionKind = {
  id: "velocity_inlet",
  label: "Velocity inlet",
  family: "inlet",
  fields: [],
  needs: {},
  color: "#fff",
  glyph: "",
  arrives: null,
  available: true,
  note: "",
  params: [
    { name: "direction", rule: "", required: false, when: {}, schema: { enum: ["normal", "components", "vector"], default: "normal" } },
    { name: "velocity", rule: "positive", required: true, when: { direction: ["normal", "vector"] }, schema: { "x-kind": "quantity" } },
    { name: "velocity_x", rule: "", required: true, when: { direction: ["components"] }, schema: { "x-kind": "quantity", default: 0 } },
    { name: "velocity_y", rule: "", required: true, when: { direction: ["components"] }, schema: { "x-kind": "quantity", default: 0 } },
    { name: "temperature", rule: "positive", required: false, when: {}, schema: { "x-kind": "quantity", default: 300 } },
  ],
};

describe("submittedValues", () => {
  it("leaves out the components of a normal-direction inlet, which the server refuses", () => {
    const values = { ...defaultValues(VELOCITY), velocity: 0.1 };
    expect(values).toMatchObject({ velocity_x: 0, velocity_y: 0 }); // the form's own start
    expect(submittedValues(VELOCITY, values)).toEqual({ direction: "normal", velocity: 0.1, temperature: 300 });
  });

  it("sends the components, and not the normal velocity, once the direction says components", () => {
    const values = { ...defaultValues(VELOCITY), direction: "components", velocity_x: 2 };
    expect(submittedValues(VELOCITY, values)).toEqual({ direction: "components", velocity_x: 2, velocity_y: 0, temperature: 300 });
  });

  it("leaves out an empty value, so the server's default stands", () => {
    expect(submittedValues(VELOCITY, { direction: "normal", velocity: "", temperature: null })).toEqual({ direction: "normal" });
  });
});
