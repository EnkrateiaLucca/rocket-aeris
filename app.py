# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "matplotlib", "vtk"]
# ///
"""Local web UI for the AERIS EMF analyzer: pick a board, simulate, browse heatmaps. Run: uv run app.py"""
import json
import re
import subprocess
import threading
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import analyze
import heatmap
import report_html

ROOT = Path(__file__).parent
BOARDS = ROOT / "boards"
PORT = 8000

# ponytail: one simulation at a time, state in memory. openEMS already uses every core; add a queue if that changes.
job = {"board": None, "state": "idle", "log": deque(maxlen=200)}
job_lock = threading.Lock()
render_lock = threading.Lock()


def boards():
    return sorted(p.name for p in BOARDS.iterdir() if (p / "simulation.json").is_file())


def progress(board):
    """Percent done, from the last 'Timestep: N' line openEMS printed."""
    steps = [int(m.group(1)) for line in job["log"] if (m := re.search(r"Timestep:\s+(\d+)", line))]
    if not steps:
        return 0
    max_steps = json.loads((BOARDS / board / "simulation.json").read_text()).get("max_steps", 0)
    return min(100, round(100 * steps[-1] / max_steps)) if max_steps else 0


def roi_env(board):
    """docker args that limit the simulation to the board's roi.json window, if it has one."""
    path = BOARDS / board / "roi.json"
    if not path.exists():
        return []
    roi = json.loads(path.read_text())
    return ["-e", f"G2E_WINDOW_MM={roi['x1'] - roi['x0']}x{roi['y1'] - roi['y0']}"]


def simulate(board):
    # --name: a second simulation of the same board (from here or from run.sh) fails fast instead of corrupting ems/.
    cmd = ["docker", "run", "--rm", "--name", f"g2e-{board}", *roi_env(board), "-w", "/work",
           "--mount", f"type=bind,source={BOARDS / board},target=/work", "gerber2ems", "-a", "--export-field"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        for line in proc.stdout:
            job["log"].append(line.rstrip())
        if proc.wait() != 0:
            job["state"] = "error"
            return
        # Render every layer now (~15 s each) so switching layers in the UI is instant.
        for layer in heatmap.layers(BOARDS / board):
            job["log"].append(f"Rendering heatmap: {layer}")
            map_png(board, layer)
        job["log"].append("Checking components against their limits")
        report(board)
        job["state"] = "done"
    except Exception as e:  # any failure must end the job, or the UI stays locked on "running"
        job["log"].append(str(e))
        job["state"] = "error"


def copper_layers(board):
    """Copper layer names from fab/*-<name>_Cu.gbr, ordered top to bottom."""
    names = {p.stem.rpartition("-")[2] for p in (BOARDS / board / "fab").glob("*_Cu.gbr")}
    return sorted(names, key=lambda n: (n != "F_Cu", n == "B_Cu", [int(d) for d in re.findall(r"\d+", n)]))


def board_info(board):
    sim = json.loads((BOARDS / board / "simulation.json").read_text())
    f = sim.get("frequency", {})
    return {"copper_layers": len(copper_layers(board)), "ports": len(sim.get("ports", [])),
            "f_start_ghz": f.get("start", 0) / 1e9, "f_stop_ghz": f.get("stop", 0) / 1e9}


def board_png(board, layer):
    """Cached picture of one copper layer + outline, drawn by the gerbv that ships in the simulation image."""
    fab = BOARDS / board / "fab"
    out = BOARDS / board / "preview" / f"{layer}.png"
    # No drill file: KiCad exports it with a different origin than the Gerbers, which blows up the image bounds.
    files = [*fab.glob(f"*-{layer}.gbr"), *fab.glob("*-Edge_Cuts.gbr")]
    colours = ["#ff6a13ff", "#9a9ba6ff"]
    with render_lock:
        if not out.exists() or out.stat().st_mtime < max(p.stat().st_mtime for p in files):
            out.parent.mkdir(exist_ok=True)
            cmd = ["docker", "run", "--rm", "--entrypoint", "gerbv", "-w", "/work/fab",
                   "--mount", f"type=bind,source={BOARDS / board},target=/work", "gerber2ems",
                   *[p.name for p in files], "--background=#16171b", *[f"--foreground={c}" for c in colours],
                   "-o", f"/work/preview/{layer}.png", "--dpi=1000", "--border=3", "--export=png", "-a"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            if r.returncode != 0 or not out.exists():
                raise RuntimeError(r.stderr.strip() or "gerbv produced no image")
    return out


def report(board):
    """The board's report.json, rebuilt when any input or simulation result is newer than it."""
    board_dir = BOARDS / board
    out = board_dir / "report.json"
    sources = [board_dir / n for n in ("netlist.d356", "bom.txt", "limits.json", "conditions.json")]
    sources += list(board_dir.glob("ems/simulation/*/port_ut_0A"))
    newest = max((p.stat().st_mtime for p in sources if p.exists()), default=0)
    with render_lock:
        if not out.exists() or out.stat().st_mtime < newest:
            analyze.run(board_dir)
    return json.loads(out.read_text())


def map_png(board, layer):
    """Cached heatmap; re-rendered when a newer simulation has replaced the dumps."""
    board_dir = BOARDS / board
    out = board_dir / "ems" / "maps" / f"{layer}.png"
    newest = max(p.stat().st_mtime for p in [*board_dir.glob(f"**/e_field_{layer}_*.vtr"), Path(heatmap.__file__)])
    with render_lock:
        if not out.exists() or out.stat().st_mtime < newest:
            heatmap.render(board_dir, layer, out)
    return out


class Handler(BaseHTTPRequestHandler):
    def send(self, body, ctype="application/json", code=200):
        if not isinstance(body, bytes):
            body = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def board(self, q):
        """Board name from the query, only if it is a real board folder (blocks path traversal)."""
        name = q.get("board", [""])[0]
        return name if name in boards() else None

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        if url.path == "/":
            return self.send((ROOT / "index.html").read_bytes(), "text/html; charset=utf-8")
        if url.path == "/api/boards":
            return self.send(boards())
        board = self.board(q)
        if not board:
            return self.send({"error": "unknown board"}, code=404)
        results = BOARDS / board / "ems" / "results"
        if url.path == "/api/status":
            running = job["state"] == "running"
            mine = job["board"] == board
            return self.send({
                "state": job["state"] if mine else "idle",
                "busy": running and not mine,
                "progress": progress(board) if mine and running else 0,
                "log": list(job["log"])[-8:] if mine else [],
                "copper": copper_layers(board),
                "info": board_info(board),
                "layers": [] if mine and running else heatmap.layers(BOARDS / board),
                "plots": [] if mine and running else sorted(p.name for p in results.glob("*.png")),
            })
        if url.path == "/api/report":
            if job["state"] == "running" and job["board"] == board:
                return self.send({"running": True})
            return self.send(report(board))
        if url.path == "/report.html":
            if job["state"] == "running" and job["board"] == board:
                return self.send(b"The simulation is still running. Export the report when it finishes.", "text/plain", 409)
            try:
                board_png(board, "F_Cu")  # the report shows the top copper for reference
            except (RuntimeError, OSError, ValueError, subprocess.TimeoutExpired):
                pass
            html = report_html.build(BOARDS / board, report(board)).encode()
            (BOARDS / board / "report.html").write_bytes(html)  # kept next to the inputs, ready to email
            return self.send(html, "text/html; charset=utf-8")
        if url.path == "/board":
            layer = q.get("layer", [""])[0]
            if layer not in copper_layers(board):
                return self.send({"error": "unknown layer"}, code=404)
            try:
                return self.send(board_png(board, layer).read_bytes(), "image/png")
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as e:
                return self.send({"error": str(e)}, code=503)  # usually: Docker is not running
        if url.path == "/map":
            layer = q.get("layer", [""])[0]
            if layer not in heatmap.layers(BOARDS / board):
                return self.send({"error": "unknown layer"}, code=404)
            return self.send(map_png(board, layer).read_bytes(), "image/png")
        if url.path == "/plot":
            name = q.get("name", [""])[0]
            if name not in {p.name for p in results.glob("*.png")}:
                return self.send({"error": "unknown plot"}, code=404)
            return self.send((results / name).read_bytes(), "image/png")
        self.send({"error": "not found"}, code=404)

    def do_POST(self):
        url = urlparse(self.path)
        board = self.board(parse_qs(url.query))
        if url.path != "/api/simulate" or not board:
            return self.send({"error": "not found"}, code=404)
        if self.headers.get("Origin") not in (None, f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"):
            return self.send({"error": "cross-origin"}, code=403)  # other websites must not start simulations
        with job_lock:
            if job["state"] == "running":
                return self.send({"error": f"already simulating {job['board']}"}, code=409)
            job.update(board=board, state="running")
            job["log"].clear()
        threading.Thread(target=simulate, args=(board,), daemon=True).start()
        self.send({"ok": True})

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)  # localhost only: this endpoint launches docker
    print(f"http://127.0.0.1:{PORT}")
    webbrowser.open(f"http://127.0.0.1:{PORT}")
    server.serve_forever()
