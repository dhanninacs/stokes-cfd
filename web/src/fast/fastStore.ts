/**
 * Fast mode's view state (ADR-0047): which value colours the plane, and the last status and frame the
 * server sent. Not model state (ADR-0013): the panel writes it, the viewport's `FastLayer` reads it and
 * writes back the range its colours span (for the panel's legend).
 */
import { create } from "zustand";
import type { FastField, FastFrame, FastRange, FastStatus } from "./fastModel";

interface FastView {
  field: FastField;
  status: FastStatus | null;
  frame: FastFrame | null;
  warning: string;
  /** What the plane's colours span now, or null while no plane is drawn. */
  range: FastRange | null;
  setField: (field: FastField) => void;
  setStatus: (status: FastStatus | null) => void;
  setFrame: (frame: FastFrame | null) => void;
  setWarning: (warning: string) => void;
  setRange: (range: FastRange | null) => void;
}

export const useFastStore = create<FastView>((set) => ({
  field: "U",
  status: null,
  frame: null,
  warning: "",
  range: null,
  setField: (field) => set({ field }),
  setStatus: (status) => set({ status }),
  setFrame: (frame) => set({ frame }),
  setWarning: (warning) => set({ warning }),
  setRange: (range) => set((s) => (s.range?.field === range?.field && s.range?.min === range?.min && s.range?.max === range?.max ? s : { range })),
}));
