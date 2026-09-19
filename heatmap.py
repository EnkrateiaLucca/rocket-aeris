# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "matplotlib", "vtk"]
# ///
"""Colour EMF heatmap from gerber2ems E-field dumps (peak |E| over time)."""
import argparse
import re
from pathlib import Path

import numpy as np


def peak_magnitude(frames):
    """Peak |E| per cell over an iterable of (N, 3) vector-field frames."""
    peak = None
    for f in frames:
        mag = np.linalg.norm(f, axis=1)
        peak = mag if peak is None else np.maximum(peak, mag)
    return peak


def read_vtr(path):
    import vtk
    from vtk.util.numpy_support import vtk_to_numpy

    r = vtk.vtkXMLRectilinearGridReader()
    r.SetFileName(str(path))
    r.Update()
    out = r.GetOutput()
    x, y = vtk_to_numpy(out.GetXCoordinates()), vtk_to_numpy(out.GetYCoordinates())
    return x, y, vtk_to_numpy(out.GetPointData().GetArray(0))


def layers(board_dir):
    """Names of the E-field dump layers a simulation left in board_dir."""
    return sorted({re.sub(r"^e_field_|_\d+$", "", p.stem) for p in Path(board_dir).glob("**/e_field_*.vtr")})


def peak_field(board_dir, layer="top_over"):
    """(x, y, peak) for one layer: coordinates in metres, peak |E| in simulated V/m as a (len(x), len(y)) array."""
    board_dir = Path(board_dir)
    files = sorted(board_dir.glob(f"**/e_field_{layer}_*.vtr"))
    if not files:
        raise ValueError(f"No dumps for layer '{layer}'. Available: {layers(board_dir) or 'none (run with --export-field)'}")
    # Reading hundreds of dump files takes ~15 s per layer: keep the result next to them until a new simulation lands.
    cache = files[0].parent / f"peak_{layer}.npz"
    if cache.exists() and cache.stat().st_mtime > max(f.stat().st_mtime for f in files):
        d = np.load(cache)
        return d["x"], d["y"], d["peak"]
    x, y, _ = read_vtr(files[0])
    peak = peak_magnitude(read_vtr(f)[2] for f in files).reshape((len(x), len(y)), order="F")
    np.savez(cache, x=x, y=y, peak=peak)
    return x, y, peak


def render(board_dir, layer="top_over", out=None):
    """Write the peak-|E| heatmap PNG for one layer; returns its path."""
    board_dir = Path(board_dir)

    x, y, peak = peak_field(board_dir, layer)

    # Figure API instead of pyplot: no global state, safe to call from the web app's threads.
    from matplotlib.colors import LogNorm
    from matplotlib.figure import Figure

    # Log scale: fields span orders of magnitude; floor at 1e-4 of max keeps noise out of the colour range.
    vmax = peak.max()
    aspect = (y[-1] - y[0]) / (x[-1] - x[0])
    tall = aspect > 0.6  # portrait or square region: colour bar goes on the side instead of underneath
    fig = Figure(figsize=(9, min(11, max(5, 8 * aspect))) if tall else (12, 4))
    ax = fig.subplots()
    im = ax.pcolormesh(x * 1e3, y * 1e3, peak.T, norm=LogNorm(vmin=vmax * 1e-4, vmax=vmax), cmap="inferno", shading="nearest")
    ax.set_aspect("equal")
    ax.set_facecolor("black")  # cells with exactly zero field have no log colour
    ax.set_title(f"Peak |E| — {board_dir.name} — layer {layer}")
    ax.set_xlabel("x (mm)")
    ax.set_ylabel("y (mm)")
    fig.colorbar(im, label="|E| (V/m)", **({"orientation": "vertical", "shrink": 0.8} if tall else
                                          {"orientation": "horizontal", "pad": 0.25}))
    out = Path(out) if out else board_dir / "emf_map.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150, bbox_inches="tight")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("board_dir", nargs="?", type=Path)
    ap.add_argument("--layer", default="top_over", help="dump name after 'e_field_'")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        a = np.array([[3.0, 4.0, 0.0], [0.0, 0.0, 1.0]])
        b = np.array([[0.0, 0.0, 1.0], [0.0, 6.0, 8.0]])
        assert np.allclose(peak_magnitude([a, b]), [5.0, 10.0])
        print("selftest ok")
        return
    if args.board_dir is None:
        ap.error("board_dir is required")
    try:
        print(render(args.board_dir, args.layer))
    except ValueError as e:
        raise SystemExit(str(e))


if __name__ == "__main__":
    main()
