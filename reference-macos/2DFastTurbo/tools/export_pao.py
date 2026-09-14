"""Export the Python initializer only; no Python timesteps are run here."""

import contextlib
import hashlib
import io
import json
from pathlib import Path
import sys

import numpy as np


def main():
    source, output = map(Path, sys.argv[1:3])
    n, re, k0, cfl, seed = int(sys.argv[3]), float(sys.argv[4]), float(sys.argv[5]), float(sys.argv[6]), int(sys.argv[7])
    sys.path.insert(0, str(source.resolve()))
    capture = io.StringIO()
    with contextlib.redirect_stdout(capture):
        from palinstrophy import turbo_simulator as dns
        state = dns.create_dns_state(N=n, Re=re, K0=k0, CFL=cfl, backend="cpu",
                                     seed=seed, start_spectrum="PAO", populate_compact_ur=False)
    output.mkdir(parents=True, exist_ok=True)
    arrays = {
        "velocity": np.ascontiguousarray(state.uc_full[:2, :n, :n // 2], dtype="<c8"),
        "omega": np.ascontiguousarray(state.om2, dtype="<c8"),
    }
    hashes = {}
    for name, array in arrays.items():
        data = array.tobytes()
        (output / (name + ".c64")).write_bytes(data)
        hashes[name] = hashlib.sha256(data).hexdigest()
    # Preserve the shared initializer's printed diagnostics, while the native
    # runner identifies the generator and actual execution backend separately.
    lines = capture.getvalue().splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith(" N           ="))
    end = next(i for i, line in enumerate(lines) if "PAO INITIALIZATION OK." in line)
    metadata = {
        "schema": 1, "n": n, "re": re, "k0": k0, "cfl": cfl, "seed": seed,
        "viscosity": float(state.visc), "diagnostics": lines[start:end + 1],
        "source": str(Path(dns.__file__).resolve()),
        "source_sha256": hashlib.sha256(Path(dns.__file__).read_bytes()).hexdigest(),
        "array_sha256": hashes,
        "layout": "little-endian complex64; velocity [2,N,N/2], omega [N,N/2]",
        "vorticity_convention": "omega = dy(u) - dx(v), matching palinstrophy",
    }
    (output / "fixture.json").write_text(json.dumps(metadata, indent=2) + "\n")


if __name__ == "__main__":
    main()
