# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "matplotlib", "vtk"]
# ///
"""Validate a board against its components' limits. Writes boards/<name>/report.json.

Inputs (spec):  netlist.d356, bom.txt, datasheets/*.pdf (via limits.json, see extract_limits.py), fab/*.gbr (via the simulation).
Extra input:    conditions.json, the board's working voltages and currents. No spec input carries them.
Outputs (spec): valid (bool), problems (text), capacity % per component, EMF map (rendered by heatmap.py).
Usage: uv run analyze.py boards/<name>   |   uv run analyze.py --selftest
"""
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

OPTIMUM, WARN, FAIL, LOW = 80, 90, 100, 30  # capacity %: spec optimum; margin warning; limit; "lightly loaded"
BREAKDOWN_V_PER_M = {"air": 3e6, "FR4": 20e6}  # dielectric strength: air ~3 kV/mm, FR4 ~20 kV/mm
SERIES_OHMS = 10  # a 2-pin part at or below this resistance carries its net's DC voltage to the other side


def parse_d356(text):
    """IPC-D-356 pads: [{net, ref, pin, x, y}] in mm. KiCad writes 'UNITS CUST 0' = 1/10000 inch."""
    pads = []
    for line in text.splitlines():
        m = re.match(r"^3[12]7(.{14})\s+(\S+)\s*-\s*(\S+).*?X([+-]\d+)Y([+-]\d+)", line)
        if m:
            net, ref, pin, x, y = m.groups()
            pads.append({"net": net.strip(), "ref": ref, "pin": pin, "x": int(x) * 0.00254, "y": int(y) * 0.00254})
    return pads


def parse_bom(text):
    rows = [line.split("\t") for line in text.splitlines()[1:] if line.strip()]
    return {r[0]: {"part": r[1], "footprint": r[2] if len(r) > 2 else ""} for r in rows}


def parse_ohms(value):
    """'5k' -> 5000, '10R' -> 10, '0.015R' -> 0.015, '10kR' -> 10000, '33.2k' -> 33200, '4k7' -> 4700."""
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([RkKmM]?)(\d*)R?", value.strip().replace("Ω", "R"))
    if not m:
        return None
    number, unit, frac = m.groups()
    scale = {"": 1, "R": 1, "k": 1e3, "K": 1e3, "M": 1e6, "m": 1e-3}[unit]
    return float(f"{number}.{frac}" if frac else number) * scale


def net_voltages(conditions, nets):
    """Known DC voltage per net: stated in conditions.json, else read from rail names like +3V3, +12V, GND."""
    volts = {}
    for net in nets:
        m = re.fullmatch(r"\+?(\d+)V(\d*)", net)
        if m:
            volts[net] = float(f"{m.group(1)}.{m.group(2) or 0}")
        elif net.upper() in ("GND", "AGND", "DGND"):
            volts[net] = 0.0
    volts.update(conditions.get("nets", {}))
    return volts


def limits_for(ref, item, limits):
    """(limits dict, source) for one BOM row: its datasheet entry, else the default for its footprint class."""
    if item["part"] in limits.get("parts", {}):
        entry = limits["parts"][item["part"]]
        return entry, f"datasheet {entry.get('datasheet', '')}".strip()
    for pattern, entry in limits.get("defaults", {}).items():
        if re.search(pattern, item["footprint"]):
            return entry, "assumed"
    return None, None


def analyze(pads, bom, limits, conditions):
    pins = defaultdict(dict)
    for p in pads:
        if p["ref"] in bom:
            pins[p["ref"]][p["pin"]] = p["net"]
    volts = net_voltages(conditions, {p["net"] for p in pads})

    def series(ref):
        lim, _ = limits_for(ref, bom[ref], limits)
        ohms = parse_ohms(bom[ref]["part"])
        return len(pins[ref]) == 2 and ((ohms is not None and ohms <= SERIES_OHMS) or
                                        (lim or {}).get("kind") in ("fuse", "inductor", "ferrite"))

    changed = True
    while changed:  # carry rail voltages across fuses, ferrites, inductors and sense resistors
        changed = False
        for ref in pins:
            if series(ref):
                a, b = pins[ref].values()
                for known, unknown in ((a, b), (b, a)):
                    if known in volts and unknown not in volts:
                        volts[unknown] = volts[known]
                        changed = True

    rows, problems, skipped, assumed = [], [], [], defaultdict(list)
    for ref in sorted(bom, key=lambda r: (re.sub(r"\d", "", r), int(re.sub(r"\D", "", r) or 0))):
        item, nets = bom[ref], list(pins[ref].values())
        lim, source = limits_for(ref, item, limits)
        if lim is None:
            skipped.append(ref)
            continue
        known = [volts[n] for n in nets if n in volts]
        amps = conditions.get("currents", {}).get(ref)
        ohms = parse_ohms(item["part"]) or lim.get("ohms")
        # Worst case across a 2-pin part: a side with unknown voltage is taken as 0 V.
        v_use = (max(known) - (min(known) if len(known) == len(nets) else 0.0)) if known and len(nets) == 2 \
            else (max(known) if known else None)
        p_use = None
        if ohms:
            p_use = amps ** 2 * ohms if amps is not None else (v_use ** 2 / ohms if v_use and ohms > SERIES_OHMS else None)
        checks = [("voltage", v_use, lim.get("v_max"), "V"), ("current", amps, lim.get("i_max"), "A"),
                  ("power", p_use, lim.get("p_max"), "W")]
        done = [{"check": c, "use": round(u, 4), "limit": m, "unit": unit, "capacity": round(100 * u / m, 1)}
                for c, u, m, unit in checks if u is not None and m]
        if not done:
            skipped.append(ref)
            continue
        worst = max(done, key=lambda d: d["capacity"])
        status = "fail" if worst["capacity"] > FAIL else "warn" if worst["capacity"] > WARN else \
            "low" if worst["capacity"] < LOW else "ok"
        rows.append({"ref": ref, "part": item["part"], **worst, "status": status, "source": source, "checks": done})
        what = f"{ref} ({item['part']}): {worst['check']} {worst['use']} {worst['unit']} is {worst['capacity']}% of its {worst['limit']} {worst['unit']} limit"
        if status == "fail":
            problems.append({"level": "error", "text": f"{what}. Over the limit."})
        elif status == "warn":
            problems.append({"level": "warning", "text": f"{what}. Margin is under {100 - WARN}%; the target is about {OPTIMUM}%."})
        if source == "assumed" and worst["capacity"] > OPTIMUM / 2:
            assumed[f"{worst['limit']} {worst['unit']}"].append(ref)

    for limit, refs in assumed.items():  # one line per assumed limit, not one per part
        problems.append({"level": "warning", "text": f"{', '.join(refs)}: no manufacturer part number in the BOM, so the "
                         f"{limit} limit is an assumption. Add part numbers to verify."})
    missing = sorted({bom[r]["part"] for r in skipped if re.match(r"^(U|Q|F|L|FB)\d", r) and len(pins[r])})
    for part in missing:
        problems.append({"level": "warning", "text": f"{part}: no datasheet limits or no working conditions. Not verified."})
    return {"rows": rows, "problems": problems, "skipped": skipped, "net_voltages": volts}


def emf(board, pads, conditions):
    """Scale the simulated field to the driven net's real voltage; check insulation; rank exposed components."""
    import numpy as np

    import heatmap

    drive = conditions.get("simulated_net", {})
    if "volts" not in drive or "top_over" not in heatmap.layers(board):
        return None
    port = np.loadtxt(next(board.glob("ems/simulation/*/port_ut_0A")), comments="%")
    scale = drive["volts"] / np.abs(port[:, 1]).max()  # FDTD is linear: field scales with the driving voltage
    out = {"net": drive.get("name", ""), "volts": drive["volts"], "layers": [], "exposed": [], "problems": []}
    for layer in heatmap.layers(board):
        x, y, peak = heatmap.peak_field(board, layer)
        medium = "air" if layer.endswith("_over") else "FR4"
        e_max = float(peak.max() * scale)
        cap = 100 * e_max / BREAKDOWN_V_PER_M[medium]
        out["layers"].append({"layer": layer, "medium": medium, "e_max_v_per_m": round(e_max), "capacity": round(cap, 3)})
        if cap > FAIL:
            out["problems"].append({"level": "error", "text": f"Field on {layer} reaches {e_max / 1e6:.2f} MV/m, above the "
                                    f"{BREAKDOWN_V_PER_M[medium] / 1e6:.0f} MV/m breakdown strength of {medium}."})
        if layer == "top_over":
            field = {}
            for p in pads:  # strongest field over any pad of each component
                xm, ym = p["x"] / 1e3, p["y"] / 1e3
                if x[0] <= xm <= x[-1] and y[0] <= ym <= y[-1]:
                    v = float(peak[np.abs(x - xm).argmin(), np.abs(y - ym).argmin()] * scale)
                    field[p["ref"]] = max(field.get(p["ref"], 0.0), v)
            top = sorted(field.items(), key=lambda kv: -kv[1])[:8]
            out["exposed"] = [{"ref": r, "e_v_per_m": round(v)} for r, v in top if r != "VIA"]
    return out


def run(board):
    board = Path(board)
    load = lambda name, default: json.loads((board / name).read_text()) if (board / name).exists() else default
    inputs = {"gerber": sorted(p.name for p in board.glob("fab/*.gbr")), "netlist": (board / "netlist.d356").exists(),
              "bom": (board / "bom.txt").exists(), "datasheets": sorted(p.name for p in board.glob("datasheets/*.pdf"))}
    report = {"board": board.name, "inputs": inputs, "valid": None, "problems": [], "rows": [], "emf": None}
    if inputs["netlist"] and inputs["bom"]:
        pads = parse_d356((board / "netlist.d356").read_text())
        bom = parse_bom((board / "bom.txt").read_text())
        conditions, limits = load("conditions.json", {}), load("limits.json", {})
        report.update(analyze(pads, bom, limits, conditions))
        report["counts"] = {"pads": len(pads), "nets": len({p["net"] for p in pads}), "parts": len(bom),
                            "limits": len(limits.get("parts", {}))}
        report["conditions"] = conditions
        report["emf"] = emf(board, pads, conditions)
        if report["emf"]:
            report["problems"] += report["emf"].pop("problems")
        report["valid"] = not any(p["level"] == "error" for p in report["problems"])
    else:
        report["problems"].append({"level": "warning", "text": "No netlist or BOM in this board folder: only the EMF map is available."})
    (board / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    return report


def selftest():
    assert [parse_ohms(v) for v in ("5k", "10R", "0.015R", "10kR", "33.2k", "4k7", "10uF")] == \
        [5000, 10, 0.015, 10000, 33200, 4700, None]
    d356 = ("327+12V             U1    -1          A01X+010000Y+020000X0630Y0118R000S2\n"
            "327GND              U1    -2          A01X+010000Y+021000X0630Y0118R000S2\n"
            "327+12V             F1    -1          A01X+030000Y+020000X0630Y0118R000S2\n"
            "327VIN              F1    -2          A01X+031000Y+020000X0630Y0118R000S2\n"
            "327VIN              R1    -1          A01X+040000Y+020000X0630Y0118R000S2\n"
            "327SIG              R1    -2          A01X+041000Y+020000X0630Y0118R000S2\n")
    pads = parse_d356(d356)
    assert len(pads) == 6 and abs(pads[0]["x"] - 25.4) < 1e-9
    bom = parse_bom("Ref\tPart\tFootprint\nU1\tLDO13\tSOT\nF1\tFUSE6\tF2920\nR1\t100R\tR_0805\n")
    limits = {"parts": {"LDO13": {"kind": "regulator", "v_max": 13}, "FUSE6": {"kind": "fuse", "i_max": 6, "v_max": 24}},
              "defaults": {"R_0805": {"kind": "resistor", "p_max": 0.125}}}
    r = analyze(pads, bom, limits, {"currents": {"F1": 7.0}})
    by = {row["ref"]: row for row in r["rows"]}
    assert by["U1"]["capacity"] == 92.3 and by["U1"]["status"] == "warn"      # 12 V on a 13 V part
    assert by["F1"]["capacity"] == 116.7 and by["F1"]["status"] == "fail"     # 7 A through a 6 A fuse
    assert r["net_voltages"]["VIN"] == 12.0                                   # rail carried across the fuse
    assert by["R1"]["check"] == "power" and by["R1"]["capacity"] > 100        # 12 V across 100 R = 1.44 W on 0.125 W
    assert sum(p["level"] == "error" for p in r["problems"]) == 2
    print("selftest ok")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    else:
        rep = run(sys.argv[1])
        print("valid:", rep["valid"], "| components rated:", len(rep["rows"]), "| problems:", len(rep["problems"]))
        for p in rep["problems"]:
            print(f"  [{p['level']}] {p['text']}")
