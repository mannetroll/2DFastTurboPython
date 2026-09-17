"""Validate Linux GPU PNG colors against the independent reference equations."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from fastturbo import Configuration, Engine, get_backend_info


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('evidence/linux-native/colors'))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    cases = []
    with Engine(Configuration(resolution=32, reynolds=50000, fixed_dt=.002)) as engine:
        engine.advance(10)
        engine.export(fields=args.output/'fields.f32', spectrum=args.output/'before.c64')
        for field, name in enumerate(('vorticity', 'speed', 'enstrophy')):
            for palette, color in enumerate(('aurora', 'ember', 'glacier', 'inferno')):
                for mode, color_range in enumerate(('sigma', 'exposure')):
                    filename=f'{name}-{color}-{color_range}.png'
                    engine.export(png=args.output/filename, display=name, palette=color,
                                  color_range=color_range, sigma=2.5, exposure=1.2, pixel_size=64)
                    cases.append(dict(file=filename,field=field,palette=palette,range=mode,sigma=2.5,exposure=1.2))
        engine.export(spectrum=args.output/'after.c64')
        assert (args.output/'before.c64').read_bytes() == (args.output/'after.c64').read_bytes()
    (args.output/'manifest.json').write_text(json.dumps(cases,indent=2)+'\n')
    validator=Path(__file__).resolve().parents[2]/'reference-windows/tests/render_reference.py'
    subprocess.run([sys.executable,str(validator),'--directory',str(args.output),'--n','32'],check=True)
    (args.output/'hardware.json').write_text(json.dumps(get_backend_info(),indent=2)+'\n')


if __name__ == '__main__':
    main()
