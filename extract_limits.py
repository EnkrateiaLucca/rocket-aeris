# /// script
# requires-python = ">=3.10"
# dependencies = ["pypdf[crypto]"]  # crypto: many datasheets are AES-encrypted
# ///
"""Read boards/<name>/datasheets/*.pdf and write the components' limits to boards/<name>/limits.json.

Each datasheet's "absolute maximum ratings" pages go to Claude (the `claude` CLI, already logged in),
which returns the limits as JSON. Parts already in limits.json are skipped, so hand corrections survive.
Usage: uv run extract_limits.py boards/<name> [--force]
"""
import json
import logging
import re
import shutil
import subprocess
import sys
from pathlib import Path

from pypdf import PdfReader

logging.getLogger("pypdf").setLevel(logging.ERROR)  # font-encoding warnings are irrelevant for text search

CLAUDE = shutil.which("claude") or str(Path.home() / ".claude/local/claude")
KEYWORDS = re.compile(r"absolute\s+maximum|maximum\s+ratings|limiting\s+values|rated\s+(voltage|current|power)|"
                      r"power\s+rating|hold\s+current|I\s?hold|V\s?max", re.I)
MAX_PAGES = 5

PROMPT = """You are reading pages of an electronic component datasheet for part number "{part}".
Extract the limits a PCB designer must not exceed. Reply with ONLY a JSON object, no prose, no code fence:
{{
  "kind": "<one of: mcu, regulator, relay, amplifier, transceiver, diode, fuse, inductor, ferrite, resistor, capacitor, buzzer, connector, other>",
  "v_max": <number or null>,   // V. For ICs: absolute-maximum voltage of the power supply / input pin (VDD, VCC, VIN, VS), not of signal or bus pins. For relays, diodes, fuses, capacitors, passives: rated / off-state / reverse voltage
  "i_max": <number or null>,   // A. Maximum continuous current through the main path (output, load, forward, hold or rated current)
  "p_max": <number or null>,   // W. Maximum power dissipation, if stated
  "ohms": <number or null>,    // Ohm. Resistance, only when the part is a resistor
  "source": "<page number and the exact table row(s) you used>"
}}
Rules: use the row that matches this exact part number or variant when the datasheet covers a family.
Use continuous ratings, not pulsed or surge ratings. Use null when the datasheet does not state a value. Never guess.

DATASHEET PAGES:
{pages}"""


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


def relevant_pages(pdf, part):
    reader = PdfReader(pdf)
    scored = []
    series = norm(part)[:9]  # family catalogs: favour the pages that list this part's series
    for i, page in enumerate(reader.pages[:140]):  # ratings sit in the front part, even in a 300-page MCU datasheet
        text = page.extract_text() or ""
        hits = len(KEYWORDS.findall(text)) + 5 * (series in norm(text))
        if hits:
            scored.append((hits, i, text))
    best = sorted(sorted(scored, reverse=True)[:MAX_PAGES], key=lambda t: t[1])
    if not best:  # short datasheet without the usual headings: send its first pages
        best = [(0, i, p.extract_text() or "") for i, p in enumerate(reader.pages[:3])]
    return "\n\n".join(f"--- page {i + 1} ---\n{text[:6000]}" for _, i, text in best)


def ask_claude(part, pages):
    out = subprocess.run([CLAUDE, "-p", PROMPT.format(part=part, pages=pages)],
                         capture_output=True, text=True, timeout=300, check=True).stdout
    return json.loads(out[out.index("{"): out.rindex("}") + 1])


def main():
    board = Path(sys.argv[1])
    force = "--force" in sys.argv
    limits_path = board / "limits.json"
    limits = json.loads(limits_path.read_text()) if limits_path.exists() else {"parts": {}}
    parts = {line.split("\t")[1] for line in (board / "bom.txt").read_text().splitlines()[1:] if "\t" in line}
    for pdf in sorted((board / "datasheets").glob("*.pdf")):
        matches = [p for p in parts if norm(p).startswith(norm(pdf.stem)) or norm(pdf.stem).startswith(norm(p))]
        for part in matches or [pdf.stem]:
            if part in limits["parts"] and not force:
                continue
            print(f"{pdf.name} -> {part}", flush=True)
            try:
                limits["parts"][part] = {**ask_claude(part, relevant_pages(pdf, part)), "datasheet": pdf.name}
            except Exception as e:  # one unreadable datasheet must not stop the rest
                print(f"  failed: {e}")
            limits_path.write_text(json.dumps(limits, indent=2) + "\n")


if __name__ == "__main__":
    main()
