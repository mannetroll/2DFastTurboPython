"""Freeze compact dependency, correctness and paired performance evidence."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from audit_runtime import category

root=Path(__file__).resolve().parents[2]
raw=root/'windows/artifacts/packaging'
out=root/'windows/packaging/evidence'
out.mkdir(parents=True,exist_ok=True)

def read(path):return json.loads(path.read_text(encoding='utf-8-sig'))
def write(path,value):path.write_text(json.dumps(value,indent=2)+'\n')
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()

copies={
    'original-imports.txt':raw/'baseline-audit/imports.txt',
    'packaged-imports.txt':raw/'clean-machine/gui/imports.txt',
    'outside-headless-audit.json':raw/'clean-machine/headless/audit.json',
    'outside-gui-audit.json':raw/'clean-machine/gui/audit.json',
    'clean-location.json':raw/'clean-machine/location.json',
    'clean-result.json':raw/'clean-machine/result.json',
    'gui-check.json':raw/'clean-machine/gui-check.json',
    'render-validation.json':raw/'clean-machine/colors/validation.json',
    'numerics-summary.json':raw/'numerics/summary.json',
    'native-initialization.json':raw/'native/summary.json',
    'original-comparison.json':raw/'original-comparison.json',
    'performance.json':raw/'performance/summary.json',
    'performance.csv':raw/'performance/runs.csv',
    'core-tests.log':root/'windows/build-dist/Testing/Temporary/LastTest.log',
}
for path in (raw/'numerics').glob('n*/validation.json'):
    copies[f'{path.parent.name}.json']=path
for name,source in copies.items():shutil.copyfile(source,out/name)
baseline=read(raw/'baseline-audit/audit.json')
for row in baseline['loaded_modules']:row['category']=category(row['path'])
baseline['non_system_modules']=[r for r in baseline['loaded_modules'] if r['category'] not in ('application','Windows system DLL','NVIDIA driver DLL')]
write(out/'original-audit.json',baseline)

exe=root/'windows/dist/2DFastTurbo.exe'
sha=digest(exe)
clean=read(out/'clean-result.json')
gui=read(out/'gui-check.json')
render=read(out/'render-validation.json')
numerics=read(out/'numerics-summary.json')
native=read(out/'native-initialization.json')
comparison=read(out/'original-comparison.json')
performance=read(out/'performance.json')
assert clean['passed'] and clean['permissions_restored'] and clean['sha256']==sha
assert gui['passed'] and render['passed'] and numerics['all_passed'] and native['passed'] and comparison['passed']
assert 'Test Passed.' in (out/'core-tests.log').read_text()
audits=[read(out/name) for name in ('outside-headless-audit.json','outside-gui-audit.json')]
for audit in audits:
    assert audit['exit_code']==0 and audit['sha256']==sha and audit['clean_environment']
    assert not audit['toolkit_modules'] and not audit['non_system_modules'] and audit['installation_directory_unchanged']
for run in performance['runs']:
    assert run['executable_sha256']==(sha if run['variant']=='packaged' else baseline['sha256'])
comparisons=[]
for mode in ('headless','gui'):
    original=next(r for r in performance['means'] if r['mode']==mode and r['variant']=='original')
    packaged=next(r for r in performance['means'] if r['mode']==mode and r['variant']=='packaged')
    comparisons.append({'mode':mode,'original_R_turbo':original['R_turbo'],'packaged_R_turbo':packaged['R_turbo'],
                        'original_ms_per_step':original['ms_per_step'],'packaged_ms_per_step':packaged['ms_per_step'],
                        'R_turbo_change_percent':100*(packaged['R_turbo']/original['R_turbo']-1)})
summary={'recorded_utc':datetime.now(timezone.utc).isoformat(),'single_executable':True,
         'executable_bytes':exe.stat().st_size,'executable_mib':exe.stat().st_size/1048576,'sha256':sha,
         'vkfft_preserved':True,'nvrtc':'statically embedded, including builtins and PTX compiler',
         'cuda_toolkit_install_required':False,'vc_redist_install_required':False,
         'external_runtime_categories':['Windows system components','NVIDIA display driver'],
         'clean_environment':clean,'physical_clean_OS_test':False,
         'numerical_checks':sum(g['tests'] for g in numerics['groups']),
         'gui_checks':len(gui['checks']),'render_comparisons':len(render['checks']),
         'supported_resolutions_checked':[c['n'] for c in native['checks']],
         'bitwise_identical_spectral_outputs':comparison['spectra_checked'],
         'performance':comparisons,
         'source_sha256':{str(p.relative_to(root)):digest(p) for folder in ('windows/src','windows/tools') for p in sorted((root/folder).rglob('*')) if p.is_file()},
         'evidence_sha256':{p.name:digest(p) for p in sorted(out.iterdir()) if p.is_file() and p.name!='summary.json'}}
write(out/'summary.json',summary)
print(json.dumps({k:v for k,v in summary.items() if k not in ('source_sha256','evidence_sha256')},indent=2))
