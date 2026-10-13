"""Test an isolated module checkout without installing it into a running PyGDO."""
import importlib.util
from pathlib import Path
import sys
import unittest

if len(sys.argv) not in (2, 3):
    raise SystemExit('Usage: python3 test/run_tests.py /path/to/pygdo [/path/to/pygdo-audio]')
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
spec = importlib.util.spec_from_file_location('gdo.sip', root / '__init__.py',
                                            submodule_search_locations=[str(root)])
module = importlib.util.module_from_spec(spec)
sys.modules['gdo.sip'] = module
spec.loader.exec_module(module)
if len(sys.argv) == 3:
    audio = Path(sys.argv[2]).resolve()
    spec = importlib.util.spec_from_file_location('gdo.audio', audio / '__init__.py',
                                                submodule_search_locations=[str(audio)])
    module = importlib.util.module_from_spec(spec)
    sys.modules['gdo.audio'] = module
    spec.loader.exec_module(module)
result = unittest.TextTestRunner(verbosity=2).run(
    unittest.defaultTestLoader.discover(str(root / 'test'), pattern='test_*.py'))
raise SystemExit(not result.wasSuccessful())
