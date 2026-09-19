#!/bin/bash
# Usage: ./export_kicad.sh <kicad_project_dir> <project_name> <board_name>
# Exports the spec inputs (Gerber, IPC-D-356 netlist, BOM) plus drill file from a KiCad project into boards/<board_name>/.
# Sets the drill/plot origin to the board's bottom-left corner, which the simulator requires,
# or to the corner of the region in boards/<board_name>/roi.json when that file exists.
set -euo pipefail
src="$(cd "$1" && pwd)"; proj="$2"; out="$(cd "$(dirname "$0")" && pwd)/boards/$3"
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
cp -R "$src/." "$work/"
python3 - "$work/$proj.kicad_pcb" "$out/roi.json" <<'PY'
import json, os, re, sys
p = sys.argv[1]; s = open(p).read()
xs, ys = [], []
for g in re.finditer(r'\((?:gr_line|gr_rect|gr_arc|gr_circle)\b(?:(?!\(gr_).)*?\(layer "Edge\.Cuts"\)', s, re.S):
    for x, y in re.findall(r"\((?:start|end|mid|center) ([-\d.]+) ([-\d.]+)\)", g.group(0)):
        xs.append(float(x)); ys.append(float(y))
ox, oy = min(xs), max(ys)                            # KiCad Y grows downward: bottom-left = (xmin, ymax)
if len(sys.argv) > 2 and os.path.exists(sys.argv[2]):  # roi.json: move the origin to the simulated region's corner
    roi = json.load(open(sys.argv[2])); ox, oy = ox + roi["x0"], oy - roi["y0"]
origin = f"(aux_axis_origin {round(ox, 4)} {round(oy, 4)})"
s = re.sub(r"\(aux_axis_origin [^)]*\)", "", s)
s = s.replace("(setup", "(setup\n\t\t" + origin, 1)
open(p, "w").write(s)

# Stackup for the simulator, from the board's own stackup table (mm, as KiCad stores it).
stack = re.search(r"\(stackup(.*?)\n\t\t\)", s, re.S).group(1)
layers = []
for name, body in re.findall(r'\(layer "([^"]+)"(.*?)\n\t\t\t\)', stack, re.S):
    get = lambda key: (re.search(r'\(%s "?([^")]+)"?\)' % key, body) or [None, None])[1]
    kind = get("type")
    layers.append({"name": name, "type": kind, "color": None,
                   "thickness": float(get("thickness")) if get("thickness") else None, "material": get("material"),
                   "epsilon": float(get("epsilon_r")) if get("epsilon_r") else None,
                   "lossTangent": float(get("loss_tangent")) if get("loss_tangent") else None})
os.makedirs(os.path.join(os.path.dirname(p), "out"), exist_ok=True)
json.dump({"layers": layers, "format_version": "1.0"}, open(os.path.join(os.path.dirname(p), "out", "stackup.json"), "w"), indent=4)
# KiCad names Gerbers after user layer names ("GND", "PWR"); the simulator expects In1_Cu, In2_Cu.
renames = {user: std.replace(".", "_") for std, user in re.findall(r'\(\d+ "(In\d+\.Cu)" \w+ "([^"]+)"\)', s)}
json.dump(renames, open(os.path.join(os.path.dirname(p), "out", "renames.json"), "w"))
print("origin", origin, "board mm", round(max(xs) - min(xs), 2), "x", round(max(ys) - min(ys), 2))
PY
mkdir -p "$out/fab"
K() { docker run --rm --platform linux/amd64 -v "$work:/w" -w /w kicad/kicad:9.0 kicad-cli "$@"; }
K pcb export gerbers -o out/ -l F.Cu,In1.Cu,In2.Cu,B.Cu,Edge.Cuts --use-drill-file-origin --no-protel-ext "$proj.kicad_pcb"
K pcb export drill -o out/ --drill-origin plot --excellon-separate-th -u mm "$proj.kicad_pcb"
K pcb export ipcd356 -o out/netlist.d356 "$proj.kicad_pcb"
K sch export bom -o out/bom.txt --fields 'Reference,Value,Footprint,Datasheet,${DNP}' --labels 'Ref,Part,Footprint,Datasheet,DNP' --field-delimiter $'\t' --string-delimiter '' --ref-range-delimiter '' "$proj.kicad_sch"
python3 - "$work/out" "$proj" <<'PY'
import json, os, sys
out, proj = sys.argv[1:]
for user, std in json.load(open(f"{out}/renames.json")).items():
    if os.path.exists(f"{out}/{proj}-{user}.gbr"):
        os.rename(f"{out}/{proj}-{user}.gbr", f"{out}/{proj}-{std}.gbr")
PY
cp "$work"/out/*.gbr "$work"/out/*-PTH.drl "$work/out/stackup.json" "$out/fab/"
cp "$work/out/netlist.d356" "$work/out/bom.txt" "$out/"
ls -la "$out" "$out/fab"
