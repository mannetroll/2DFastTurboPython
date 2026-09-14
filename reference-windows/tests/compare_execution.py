"""Verify that GUI snapshots/presentation do not change a fixed solver trajectory.

Run the two commands documented in windows/README.md before this comparison.
"""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
directory = root/'windows/artifacts'
reports = [json.loads((directory/f'{mode}-trajectory.json').read_text())
           for mode in ('gui', 'headless')]
spectra = [(directory/f'{mode}-trajectory.c64').read_bytes()
           for mode in ('gui', 'headless')]
assert reports[0]['stats']['steps'] == reports[1]['stats']['steps'] == 20
assert reports[0]['stats'] == reports[1]['stats'], 'GUI changed final diagnostics'
assert spectra[0] == spectra[1], 'GUI changed the spectral trajectory'
result = {'passed': True, 'comparison': 'GUI adaptive batching vs headless batch 8',
          'resolution': 128, 'steps': 20, 'dt': .001, 'bitwise_identical': True,
          'spectrum_sha256': hashlib.sha256(spectra[0]).hexdigest()}
(directory/'execution-comparison.json').write_text(json.dumps(result, indent=2)+'\n')
print('PASS GUI/headless endpoint spectra and diagnostics are bitwise identical')
