"""Compare packaged numerical outputs byte-for-byte with the saved original gate."""
import hashlib
import json
from pathlib import Path

root=Path(__file__).resolve().parents[2]
artifacts=root/'windows/artifacts'
records=[]
for group in sorted((artifacts/'packaging/numerics').glob('n*-*')):
    original=artifacts/('numerics-n128' if group.name.startswith('n128-') else 'numerics')/group.name
    for actual in sorted(group.rglob('spectrum.c64')):
        relative=actual.relative_to(group)
        reference=original/relative
        a=actual.read_bytes();b=reference.read_bytes()
        records.append({'case':str(Path(group.name)/relative),'bitwise_identical':a==b,
                        'original_sha256':hashlib.sha256(b).hexdigest(),
                        'packaged_sha256':hashlib.sha256(a).hexdigest()})
for n in (128,256,512,1024,2048,4096):
    a=(artifacts/f'packaging/native/n{n}.c64').read_bytes()
    b=(artifacts/f'native/n{n}.c64').read_bytes()
    records.append({'case':f'native-n{n}','bitwise_identical':a==b,
                    'original_sha256':hashlib.sha256(b).hexdigest(),
                    'packaged_sha256':hashlib.sha256(a).hexdigest()})
result={'passed':bool(records) and all(r['bitwise_identical'] for r in records),
        'comparison':'original vs packaged full complex64 spectra', 'spectra_checked':len(records),'cases':records}
(artifacts/'packaging/original-comparison.json').write_text(json.dumps(result,indent=2)+'\n')
print(f'{"PASS" if result["passed"] else "FAIL"}: {len(records)} original/packaged spectral outputs compared bitwise')
raise SystemExit(0 if result['passed'] else 1)
