"""Self-contained, printable HTML report for one board (images embedded, so the file can be emailed or saved as PDF)."""
import base64
import json
from datetime import date
from html import escape
from pathlib import Path

LAYER_LABELS = {"top_over": "Above board", "bottom_over": "Below board", "F_Cu": "Top copper", "B_Cu": "Bottom copper"}

CSS = """
:root { --ink:#1b1c20; --muted:#5f606b; --line:#d9d9d4; --accent:#d9540a; --good:#1a8a55; --bad:#c62828; --warn:#a97900; }
* { box-sizing:border-box; }
body { margin:0; color:var(--ink); background:#fff; font:13.5px/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; }
main { max-width:900px; margin:0 auto; padding:36px 28px 60px; }
header { border-bottom:3px solid var(--ink); padding-bottom:14px; margin-bottom:8px; }
header small { color:var(--accent); font-weight:700; letter-spacing:.12em; font-size:11px; }
h1 { margin:2px 0 2px; font-size:26px; }
.meta { color:var(--muted); }
h2 { font-size:16px; margin:30px 0 10px; padding-left:10px; border-left:4px solid var(--accent); break-after:avoid; }
h3 { font-size:13.5px; margin:18px 0 6px; break-after:avoid; }
p { margin:6px 0; } .muted { color:var(--muted); }
.verdict { display:flex; align-items:center; gap:18px; margin:18px 0 6px; padding:14px 16px; border:1px solid var(--line); border-radius:8px; }
.badge { font-size:20px; font-weight:800; letter-spacing:.04em; padding:8px 16px; border:2px solid currentColor; border-radius:8px; white-space:nowrap; }
.good { color:var(--good); } .bad { color:var(--bad); }
.kpis { display:grid; grid-template-columns:repeat(4, 1fr); gap:10px; margin-top:10px; }
.kpi { border:1px solid var(--line); border-radius:8px; padding:8px 12px; } .kpi b { display:block; font-size:20px; }
table { border-collapse:collapse; width:100%; font-size:12.5px; margin:6px 0; }
th, td { text-align:left; padding:5px 8px; border-bottom:1px solid var(--line); vertical-align:top; }
th { color:var(--muted); font-size:11px; text-transform:uppercase; letter-spacing:.05em; }
td.num { text-align:right; font-variant-numeric:tabular-nums; white-space:nowrap; }
tr { break-inside:avoid; }
.problem { padding:6px 10px; border-left:4px solid var(--warn); background:#faf8f0; margin:5px 0; break-inside:avoid; }
.problem.error { border-color:var(--bad); background:#fdf2f2; }
.bar { position:relative; width:110px; height:9px; border:1px solid var(--line); border-radius:5px; background:#f3f3f0; }
.bar i { display:block; height:100%; border-radius:4px; background:var(--good); -webkit-print-color-adjust:exact; print-color-adjust:exact; }
.bar i.warn { background:var(--warn); } .bar i.fail { background:var(--bad); } .bar i.low { background:#5b8def; }
.bar::after { content:""; position:absolute; left:80%; top:-3px; bottom:-3px; width:2px; background:var(--ink); }
.tag { font-size:10px; color:var(--muted); border:1px solid var(--line); border-radius:4px; padding:0 4px; margin-left:4px; }
figure { margin:10px 0; break-inside:avoid; } figure img { max-width:100%; border:1px solid var(--line); border-radius:6px; }
figcaption { color:var(--muted); font-size:12px; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.toolbar { position:sticky; top:0; background:#fff; border-bottom:1px solid var(--line); padding:8px 28px; text-align:right; }
.toolbar button { font:inherit; padding:7px 14px; border-radius:8px; border:1px solid var(--accent); background:var(--accent); color:#fff; font-weight:700; cursor:pointer; }
@media print { .toolbar { display:none; } main { padding:0; max-width:none; } @page { margin:16mm 14mm; } }
"""


def img(path, caption):
    if not Path(path).exists():
        return ""
    data = base64.b64encode(Path(path).read_bytes()).decode()
    return f'<figure><img alt="{escape(caption)}" src="data:image/png;base64,{data}"><figcaption>{escape(caption)}</figcaption></figure>'


def table(headers, rows):
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(c if c.startswith("<td") else f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def num(text):
    return f'<td class="num">{escape(str(text))}</td>'


def build(board_dir, report):
    board_dir = Path(board_dir)
    e = escape
    inputs, counts = report["inputs"], report.get("counts", {})
    rows = sorted(report["rows"], key=lambda r: -r["capacity"])
    errors = [p for p in report["problems"] if p["level"] == "error"]
    warnings = [p for p in report["problems"] if p["level"] != "error"]
    valid = report["valid"]
    limits = json.loads((board_dir / "limits.json").read_text()) if (board_dir / "limits.json").exists() else {}
    cond = report.get("conditions") or {}
    emf = report.get("emf")

    out = [f"<title>EMF – PCB Analysis report · {e(report['board'])}</title><style>{CSS}</style>",
           '<div class="toolbar"><button onclick="window.print()">Print / Save as PDF</button></div><main>',
           "<header><small>PROJECT ASCENT · AERIS · ISEL</small><h1>EMF – PCB Analysis report</h1>",
           f'<div class="meta">Board: <b>{e(report["board"])}</b> · {date.today().isoformat()} · simulated with openEMS</div></header>']

    badge = "NOT CHECKED" if valid is None else "VALID" if valid else "NOT VALID"
    out.append(f'<div class="verdict"><span class="badge {"good" if valid else "bad"}">{badge}</span><span>'
               + ("No netlist or BOM was supplied, so components were not checked." if valid is None else
                  f"{len(rows)} components were checked against their limits. {len(errors)} exceed a limit. "
                  f"{len(warnings)} warnings need attention.") + "</span></div>")
    if rows:
        near = sum(60 <= r["capacity"] <= 100 for r in rows)
        out.append('<div class="kpis">' + "".join(f'<div class="kpi"><b>{v}</b>{e(k)}</div>' for k, v in [
            ("components checked", len(rows)), ("over their limit", len(errors)),
            ("between 60 % and 100 %", near), ("without limits or working data", len(report.get("skipped", [])))]) + "</div>")

    out.append("<h2>1. Inputs</h2>")
    out.append(table(["Input", "Files", "Content"], [
        ["Gerber (.gbr)", e(", ".join(inputs["gerber"])) or "missing", f"{len(inputs['gerber'])} layers"],
        ["Netlist (.d356)", "netlist.d356" if inputs["netlist"] else "missing",
         f"{counts.get('pads', 0)} pads on {counts.get('nets', 0)} nets"],
        ["Bill of Materials (.txt)", "bom.txt" if inputs["bom"] else "missing", f"{counts.get('parts', 0)} components"],
        ["Datasheets (.pdf)", e(", ".join(inputs["datasheets"])) or "missing",
         f"{len(inputs['datasheets'])} PDFs; limits read for {counts.get('limits', 0)} parts"]]))
    if cond:
        out.append("<h3>Working conditions (additional input)</h3>")
        out.append(f'<p class="muted">{e(cond.get("note", ""))}</p>')
        out.append("<div class='grid2'><div>" +
                   table(["Rail / net", "Voltage"], [[e(n), num(f"{v} V")] for n, v in cond.get("nets", {}).items()]) + "</div><div>" +
                   table(["Component", "Working current"], [[e(r), num(f"{a} A")] for r, a in cond.get("currents", {}).items()]) +
                   "</div></div>")

    out.append("<h2>2. Problems found</h2>")
    out += [f'<div class="problem {p["level"]}"><b>{p["level"].capitalize()}.</b> {e(p["text"])}</div>' for p in errors + warnings] \
        or ['<p class="muted">None.</p>']

    if rows:
        out.append("<h2>3. Capacity of each component</h2>")
        out.append('<p class="muted">Working load divided by the limit from the datasheet. The vertical mark is the 80 % target. '
                   '"assumed" marks parts without a manufacturer part number, checked against a default limit.</p>')
        out.append(table(["Ref", "Part", "Check", "Working", "Limit", "Capacity", "%"], [
            [e(r["ref"]), e(r["part"]) + ('<span class="tag">assumed</span>' if r["source"] == "assumed" else ""), e(r["check"]),
             num(f"{r['use']} {r['unit']}"), num(f"{r['limit']} {r['unit']}"),
             f'<div class="bar"><i class="{r["status"]}" style="width:{min(100, r["capacity"])}%"></i></div>', num(f"{r['capacity']} %")]
            for r in rows]))

    out.append("<h2>4. Electromagnetic field map</h2>")
    if emf:
        out.append(f"<p>Simulated net: <b>{e(emf['net'])}</b>, scaled to its working voltage of {emf['volts']} V. "
                   "The field is compared with the breakdown strength of the medium (air 3 kV/mm, FR4 20 kV/mm).</p>")
        out.append(table(["Layer", "Medium", "Peak field", "Share of breakdown strength"], [
            [e(LAYER_LABELS.get(l["layer"], l["layer"].replace("_", " "))), l["medium"],
             num(f"{l['e_max_v_per_m'] / 1000:.1f} kV/m"), num(f"{l['capacity']} %")] for l in emf["layers"]]))
        exposed = [x for x in emf["exposed"] if x["e_v_per_m"] > 0]
        if exposed:
            out.append("<p>Components in the strongest field: " +
                       ", ".join(f"{e(x['ref'])} ({x['e_v_per_m'] / 1000:.1f} kV/m)" for x in exposed) + ".</p>")
    maps = board_dir / "ems" / "maps"
    out.append(img(maps / "top_over.png", "Peak electric field just above the board (simulated test pulse, logarithmic scale)")
               or '<p class="muted">No simulation results yet.</p>')
    out.append(img(maps / "F_Cu.png", "Peak electric field on the top copper layer"))
    out.append(img(board_dir / "preview" / "F_Cu.png", "Top copper layer of the board, for reference"))
    plots = sorted((board_dir / "ems" / "results").glob("*.png"))
    if plots:
        out.append("<h3>Signal integrity of the simulated net</h3><div class='grid2'>" +
                   "".join(img(p, p.stem.replace("_", " ")) for p in plots) + "</div>")

    if limits.get("parts"):
        out.append("<h2>5. Limits read from the datasheets</h2>")
        fmt = lambda v, u: num(f"{v} {u}") if v is not None else num("–")
        out.append(table(["Part", "Kind", "V max", "I max", "P max", "Source in datasheet"], [
            [e(p), e(str(l.get("kind", ""))), fmt(l.get("v_max"), "V"), fmt(l.get("i_max"), "A"), fmt(l.get("p_max"), "W"),
             e(l.get("source", ""))] for p, l in limits["parts"].items()]))
        if limits.get("defaults"):
            out.append("<h3>Assumed limits for parts without a part number</h3>")
            out.append(table(["Footprint", "V max", "P max", "Basis"], [
                [e(k), fmt(d.get("v_max"), "V"), fmt(d.get("p_max"), "W"), e(d.get("note", ""))]
                for k, d in limits["defaults"].items()]))

    out.append("<h2>Method</h2><p class='muted'>Voltage: highest rail on the component's pins, or the difference across a 2-pin part, "
               "against its rated voltage. Current: stated working current against rated current. Power: I²R or V²/R against rated "
               "power. Capacity is the worst of the three; above 100 % the board is not valid, above 90 % is a warning. "
               "Rail voltages carry across fuses, ferrites, inductors and resistors of 10 Ω or less. "
               "The field map comes from an openEMS FDTD simulation of the board's Gerber geometry; fields scale linearly with voltage.</p>")
    out.append("</main>")
    return "<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width, initial-scale=1'>" + "\n".join(out)
