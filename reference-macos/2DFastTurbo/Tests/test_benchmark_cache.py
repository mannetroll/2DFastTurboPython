"""Process-contract checks; these do not stand in for GPU numerical validation."""
from pathlib import Path
import sys
import tempfile
import textwrap
import unittest

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Scripts"))
from validate import run_solver


class BenchmarkCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.app = self.directory / "protocol_fixture"
        self.app.write_text(f"#!{sys.executable}\n" + textwrap.dedent("""\
            import json
            from pathlib import Path
            import sys
            args = dict(zip(sys.argv[2::2], sys.argv[3::2]))
            directory = Path(__file__).parent
            counter = directory / 'invocations'
            count = int(counter.read_text()) if counter.exists() else 0
            counter.write_text(str(count + 1))
            if (directory / 'legacy_only').exists() and '--operator' in args:
                raise SystemExit('Legacy adapter received an unsupported operator flag')
            operator = args.get('--operator', 'stress')
            if (directory / 'wrong_operator').exists():
                operator = 'stress'
            n = int(args['--resolution'])
            result = {'config': {'resolution': n, 'padded_resolution': 3*n//2,
                                 'operator': operator},
                      'stats': {'steps': 1, 'simulation_time': 0.1},
                      'seconds': 1, 'spectrum_normalization': 'fourier-series'}
            Path(args['--output']).write_text(json.dumps(result))
            Path(args['--spectrum']).write_bytes(bytes(n*n*8))
            print(json.dumps(result))
            """))
        self.app.chmod(0o755)
        self.field = np.zeros((16, 16), dtype=np.float32)

    def execute(self, **kwargs):
        return run_solver(self.app, self.field, self.directory / "run", steps=1,
                          resume=True, **kwargs)

    def invocations(self):
        return int((self.directory / "invocations").read_text())

    def test_resume_requires_matching_input_executable_and_command(self):
        self.assertFalse(self.execute(operator_name="stress").reused)
        self.assertTrue(self.execute(operator_name="stress").reused)
        self.assertEqual(self.invocations(), 1)
        self.field[0, 0] = 1
        self.assertFalse(self.execute(operator_name="stress").reused)
        self.app.write_text(self.app.read_text() + "\n# revised executable\n")
        self.assertFalse(self.execute(operator_name="stress").reused)
        self.assertFalse(self.execute(operator_name="advective").reused)
        self.assertEqual(self.invocations(), 4)

    def test_corrupt_spectrum_invalidates_cached_trajectory(self):
        self.execute(operator_name="stress")
        spectrum = self.directory / "run/spectrum.c64"
        spectrum.write_bytes(spectrum.read_bytes()[:-8])
        self.assertFalse(self.execute(operator_name="stress").reused)
        self.assertEqual(self.invocations(), 2)
        self.assertEqual(spectrum.stat().st_size, 16*16*8)

    def test_requested_operator_must_match_reported_actual_operator(self):
        (self.directory / "wrong_operator").touch()
        with self.assertRaisesRegex(ValueError, "Requested advective operator"):
            self.execute(operator_name="advective")
        self.assertFalse((self.directory / "run/cache.json").exists())

    def test_legacy_adapter_receives_no_operator_option(self):
        (self.directory / "legacy_only").touch()
        result = self.execute()
        self.assertNotIn("--operator", result.command)
        self.assertEqual(self.invocations(), 1)


if __name__ == "__main__":
    unittest.main()
