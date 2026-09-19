#!/bin/bash
# Usage: ./run.sh boards/<name> [layer]   — simulate the board, then render the EMF heatmap.
set -euo pipefail
board="$(cd "$1" && pwd)"
roi=()
if [ -f "$board/roi.json" ]; then  # simulate only the board's region of interest
  roi=(-e "G2E_WINDOW_MM=$(python3 -c "import json,sys; r=json.load(open(sys.argv[1])); print(f\"{r['x1']-r['x0']}x{r['y1']-r['y0']}\")" "$board/roi.json")")
fi
# --name: a second simulation of the same board fails fast instead of corrupting ems/
docker run --rm --name "g2e-$(basename "$board")" "${roi[@]}" -w /work --mount type=bind,source="$board",target=/work gerber2ems -a --export-field
uv run "$(dirname "$0")/heatmap.py" "$board" --layer "${2:-top_over}"
open "$board/emf_map.png"
