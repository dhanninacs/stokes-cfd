/** The Streams table: each stream's flow, pressure drop, temperatures and heat, and the balance line. */
import { describe, expect, it } from "vitest";
import { balanceLine, readStreams, streamRow } from "./streamsModel";

const answer = {
  streams: [
    { region: "hot", inlets: ["hot_in"], outlets: ["hot_out"], mass_flow: 0.0002125, pressure_drop: 60.986, T_in: 350, T_out: 330.6347, heat: -17.2054 },
    { region: "cold", inlets: ["cold_in"], outlets: ["cold_out"], mass_flow: 0.0001192, pressure_drop: 29.064, T_in: 300, T_out: 333.2888, heat: 16.5847 },
  ],
  balance: { gained: 16.5847, lost: 17.2054, imbalance: 0.0361 },
  units: { mass_flow: "kg/s", pressure: "Pa" },
};

describe("the Streams table", () => {
  it("reads each stream and writes its row in the card's words", () => {
    const read = readStreams(answer);
    expect(read.streams.map((s) => s.region)).toEqual(["hot", "cold"]);
    expect(streamRow(read.streams[0], read.units)).toEqual({ name: "hot", flow: "0.2125 g/s", dp: "60.99 Pa", temps: "350 → 330.63 K", heat: "-17.21 W" });
    expect(streamRow(read.streams[1], read.units).heat).toBe("+16.58 W");
  });

  it("says how much heat crossed and how well the streams agree", () => {
    expect(balanceLine(readStreams(answer).balance)).toBe("Heat exchanged 16.9 W · the streams agree to 3.6 %");
    expect(balanceLine(null)).toBeNull();
  });

  it("shows a dash for what a flow-only run cannot say, and nothing for a malformed answer", () => {
    const flowOnly = readStreams({ streams: [{ region: "fluid", inlets: ["inlet"], outlets: ["outlet"], mass_flow: 1e-5, pressure_drop: 0.2 }], units: { mass_flow: "m3/s", pressure: "m2/s2" } });
    expect(streamRow(flowOnly.streams[0], flowOnly.units)).toEqual({ name: "fluid", flow: "0.00001 m3/s", dp: "0.2 m2/s2", temps: "-", heat: "-" });
    expect(readStreams("nonsense").streams).toEqual([]);
  });
});
