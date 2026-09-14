#!/usr/bin/env python3
"""Generate the standalone Xcode project using only the Python standard library."""
from pathlib import Path
import hashlib

ROOT = Path(__file__).resolve().parents[1]
objects = {}

def ident(key):
    return hashlib.sha1(key.encode()).hexdigest()[:24].upper()

def add(key, value):
    objects[ident(key)] = value
    return ident(key)

def q(value):
    return '"' + str(value).replace('\\', '\\\\').replace('"', '\\"') + '"'

def arr(values):
    return '(' + ', '.join(values) + (',' if values else '') + ')'

def settings(values):
    return '{ ' + ' '.join(f'{k} = {v};' for k, v in values.items()) + ' }'

def configs(key, common):
    refs = []
    for name in ['Debug', 'Release']:
        extra = {'SWIFT_OPTIMIZATION_LEVEL': q('-Onone' if name == 'Debug' else '-O'),
                 'GCC_OPTIMIZATION_LEVEL': '0' if name == 'Debug' else '3',
                 'DEBUG_INFORMATION_FORMAT': q('dwarf' if name == 'Debug' else 'dwarf-with-dsym'),
                 'ENABLE_TESTABILITY': 'YES'}
        if name == 'Release':
            extra['SWIFT_COMPILATION_MODE'] = 'wholemodule'
        refs.append(add(key + name, '{ isa = XCBuildConfiguration; buildSettings = ' + settings(common | extra) + '; name = ' + name + '; }'))
    return add(key + '-configs', '{ isa = XCConfigurationList; buildConfigurations = ' + arr(refs) + '; defaultConfigurationIsVisible = 0; defaultConfigurationName = Release; }')

def reference(path, scope=''):
    kind = {'.swift': 'sourcecode.swift', '.mm': 'sourcecode.cpp.objcpp', '.cpp': 'sourcecode.cpp.cpp', '.h': 'sourcecode.c.h', '.hpp': 'sourcecode.cpp.h', '.metal': 'sourcecode.metal', '.plist': 'text.plist.xml', '.xcassets': 'folder.assetcatalog'}.get(Path(path).suffix, 'text')
    return add('file-' + scope + path, '{ isa = PBXFileReference; lastKnownFileType = ' + kind + '; path = ' + q(path) + '; sourceTree = SOURCE_ROOT; }')

def build_file(key, ref):
    extra = ' settings = { COMPILER_FLAGS = "-Wno-deprecated-declarations"; };' if key.endswith('MTTVkFFTCore.cpp') else ''
    return add('build-' + key, '{ isa = PBXBuildFile; fileRef = ' + ref + ';' + extra + ' }')

def phase(key, kind, files):
    return add(key, '{ isa = ' + kind + '; buildActionMask = 2147483647; files = ' + arr(files) + '; runOnlyForDeploymentPostprocessing = 0; }')

def paths(pattern):
    return sorted(str(p.relative_to(ROOT)) for p in ROOT.glob(pattern) if p.is_file())

core = paths('Sources/FastTurboCore/*.swift') + paths('Sources/Bridge/*.mm') + paths('Sources/Bridge/*.cpp')
tools = ['Sources/Tools/BenchmarkCLI.swift']
app = paths('Sources/FastTurboApp/*.swift') + paths('Sources/FastTurboApp/*.metal') + ['Sources/Tools/AppMain.swift']
targets = {
    '2DFastTurbo': (core + tools + app, 'application', 'wrapper.application', '.app'),
    '2DFastTurboBench': (core + tools + ['Sources/FastTurboBench/BenchMain.swift'], 'tool', 'compiled.mach-o.executable', ''),
    '2DFastTurboTests': (core + paths('Tests/*.swift'), 'bundle.unit-test', 'wrapper.cfbundle', '.xctest'),
}
targets['LegacyMetalBench'] = (paths('Benchmarks/LegacyMetal/*.swift') + ['Sources/FastTurboCore/FastTurboTypes.swift'] + paths('Sources/Bridge/*.mm') + paths('Sources/Bridge/*.cpp') + tools + ['Sources/FastTurboBench/BenchMain.swift'], 'tool', 'compiled.mach-o.executable', '')
products = {t: add('product-' + t, '{ isa = PBXFileReference; explicitFileType = ' + spec[2] + '; includeInIndex = 0; path = ' + q(t + spec[3]) + '; sourceTree = BUILT_PRODUCTS_DIR; }') for t, spec in targets.items()}
groups = []
for target, (sources, product_type, _, _) in targets.items():
    refs = [reference(p, target) for p in sources]
    groups.append(add('group-' + target, '{ isa = PBXGroup; children = ' + arr(refs) + '; name = ' + q(target) + '; sourceTree = "<group>"; }'))
    source_phase = phase(target + '-sources', 'PBXSourcesBuildPhase', [build_file(target + p, r) for p, r in zip(sources, refs)])
    phases = [source_phase, phase(target + '-frameworks', 'PBXFrameworksBuildPhase', [])]
    resources = []
    if target == '2DFastTurbo':
        for asset in ROOT.glob('Resources/*.xcassets'):
            p = str(asset.relative_to(ROOT))
            resources.append(build_file(target + p, reference(p)))
        for p in paths('Resources/*.txt'):
            resources.append(build_file(target + p, reference(p)))
    phases.append(phase(target + '-resources', 'PBXResourcesBuildPhase', resources))
    common = {
        'PRODUCT_NAME': q('$(TARGET_NAME)'), 'PRODUCT_MODULE_NAME': q(target.replace('2D', 'Fast2D')),
        'PRODUCT_BUNDLE_IDENTIFIER': q('com.mannetroll.' + target),
        'SDKROOT': 'macosx', 'MACOSX_DEPLOYMENT_TARGET': '15.0', 'ARCHS': 'arm64',
        'SUPPORTED_PLATFORMS': 'macosx', 'SWIFT_VERSION': '5.0', 'SWIFT_STRICT_CONCURRENCY': 'minimal',
        'CLANG_CXX_LANGUAGE_STANDARD': q('c++20'), 'CLANG_ENABLE_MODULES': 'YES', 'CLANG_ENABLE_OBJC_ARC': 'YES',
        'SWIFT_OBJC_BRIDGING_HEADER': 'Sources/Bridge/FastTurbo-Bridging-Header.h',
        'HEADER_SEARCH_PATHS': arr([q('$(SRCROOT)/Sources/Bridge'), q('$(SRCROOT)/ThirdParty/VkFFTSource/vkFFT'), q('$(SRCROOT)/ThirdParty/VkFFTSource/metal-cpp')]),
        'CODE_SIGN_STYLE': 'Automatic', 'CODE_SIGN_IDENTITY': q('-'),
        'ENABLE_USER_SCRIPT_SANDBOXING': 'YES', 'ENABLE_APP_SANDBOX': 'NO',
        'CURRENT_PROJECT_VERSION': '1', 'MARKETING_VERSION': '1.0', 'MTL_FAST_MATH': 'YES',
        'LD_RUNPATH_SEARCH_PATHS': q('$(inherited) @executable_path/../Frameworks @loader_path/../Frameworks'),
        'OTHER_LDFLAGS': arr([q('$(inherited)'), q('-framework'), q('Metal'), q('-framework'), q('Foundation')]),
    }
    if product_type == 'application':
        common |= {'INFOPLIST_FILE': 'Resources/Info.plist', 'GENERATE_INFOPLIST_FILE': 'NO', 'COMBINE_HIDPI_IMAGES': 'YES'}
        if (ROOT / 'Resources/Assets.xcassets/AppIcon.appiconset').exists():
            common['ASSETCATALOG_COMPILER_APPICON_NAME'] = 'AppIcon'
    else:
        common |= {'GENERATE_INFOPLIST_FILE': 'YES', 'SKIP_INSTALL': 'YES'}
    if product_type == 'bundle.unit-test':
        common['LD_RUNPATH_SEARCH_PATHS'] = q('$(inherited) @loader_path/../Frameworks $(PLATFORM_DIR)/Developer/Library/Frameworks')
    if target == 'LegacyMetalBench':
        common['SWIFT_ACTIVE_COMPILATION_CONDITIONS'] = q('$(inherited) LEGACY_METAL_REF')
    config = configs(target, common)
    add('target-' + target, '{ isa = PBXNativeTarget; buildConfigurationList = ' + config + '; buildPhases = ' + arr(phases) + '; buildRules = (); dependencies = (); name = ' + q(target) + '; productName = ' + q(target) + '; productReference = ' + products[target] + '; productType = ' + q('com.apple.product-type.' + product_type) + '; }')

groups.append(add('headers-group', '{ isa = PBXGroup; children = ' + arr([reference(p) for p in paths('Sources/Bridge/*.h') + paths('Sources/Bridge/*.hpp') + paths('Resources/*.txt')] + [reference('Resources/Info.plist'), reference('README.md')]) + '; name = Supporting; sourceTree = "<group>"; }'))
product_group = add('products', '{ isa = PBXGroup; children = ' + arr(list(products.values())) + '; name = Products; sourceTree = "<group>"; }')
groups.append(product_group)
main_group = add('main-group', '{ isa = PBXGroup; children = ' + arr(groups) + '; sourceTree = "<group>"; }')
project_config = configs('project', {'SDKROOT': 'macosx', 'MACOSX_DEPLOYMENT_TARGET': '15.0', 'CLANG_ENABLE_MODULES': 'YES'})
add('project', '{ isa = PBXProject; attributes = { BuildIndependentTargetsInParallel = YES; LastUpgradeCheck = 2630; }; buildConfigurationList = ' + project_config + '; compatibilityVersion = "Xcode 14.0"; developmentRegion = en; hasScannedForEncodings = 0; knownRegions = (en, Base,); mainGroup = ' + main_group + '; productRefGroup = ' + product_group + '; projectDirPath = ""; projectRoot = ""; targets = ' + arr([ident('target-' + t) for t in targets]) + '; }')
directory = ROOT / '2DFastTurbo.xcodeproj'
directory.mkdir(exist_ok=True)
(directory / 'project.pbxproj').write_text('// !$*UTF8*$!\n{\n archiveVersion = 1;\n classes = {};\n objectVersion = 56;\n objects = {\n' + '\n'.join(f'  {i} = {v};' for i, v in objects.items()) + '\n };\n rootObject = ' + ident('project') + ';\n}\n')
scheme_dir = directory / 'xcshareddata/xcschemes'
scheme_dir.mkdir(parents=True, exist_ok=True)
def build_ref(target):
    return f'<BuildableReference BuildableIdentifier="primary" BlueprintIdentifier="{ident("target-" + target)}" BuildableName="{target + targets[target][3]}" BlueprintName="{target}" ReferencedContainer="container:2DFastTurbo.xcodeproj"/>'

for target in ['2DFastTurbo', '2DFastTurboBench', 'LegacyMetalBench']:
    ref = build_ref(target)
    tests = '<Testables><TestableReference skipped="NO">' + build_ref('2DFastTurboTests') + '</TestableReference></Testables>'
    scheme = f'''<?xml version="1.0" encoding="UTF-8"?>
<Scheme LastUpgradeVersion="2630" version="1.3">
 <BuildAction parallelizeBuildables="YES" buildImplicitDependencies="YES"><BuildActionEntries><BuildActionEntry buildForTesting="YES" buildForRunning="YES" buildForProfiling="YES" buildForArchiving="YES" buildForAnalyzing="YES">{ref}</BuildActionEntry></BuildActionEntries></BuildAction>
 <TestAction buildConfiguration="Release" selectedDebuggerIdentifier="Xcode.DebuggerFoundation.Debugger.LLDB" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.LLDB" shouldUseLaunchSchemeArgsEnv="YES">{tests}</TestAction>
 <LaunchAction buildConfiguration="Release" selectedDebuggerIdentifier="" selectedLauncherIdentifier="Xcode.IDEFoundation.Launcher.PosixSpawn" launchStyle="0" useCustomWorkingDirectory="NO" ignoresPersistentStateOnLaunch="YES" debugDocumentVersioning="YES"><BuildableProductRunnable runnableDebuggingMode="0">{ref}</BuildableProductRunnable></LaunchAction>
 <ProfileAction buildConfiguration="Release" shouldUseLaunchSchemeArgsEnv="YES" savedToolIdentifier="" useCustomWorkingDirectory="NO"><BuildableProductRunnable runnableDebuggingMode="0">{ref}</BuildableProductRunnable></ProfileAction>
 <AnalyzeAction buildConfiguration="Debug"/><ArchiveAction buildConfiguration="Release" revealArchiveInOrganizer="YES"/>
</Scheme>
'''
    (scheme_dir / (target + '.xcscheme')).write_text(scheme)
print('Generated 2DFastTurbo.xcodeproj')
