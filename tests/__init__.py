# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Test package. Importing it registers src/ as the ``retain`` package.

The source tree is laid out for installation (src/ becomes retain/), so tests import it under
that name without a build. `python3 -m unittest discover -s tests` imports test modules as
top-level modules and never runs this file itself, so every test module starts with
``from tests import …`` (run from the repository root, as scripts/check.sh does).

It also keeps every test off the desktop's settings: GSettings goes to the memory backend,
with this tree's schema visible (tests/gtk.py's use_test_settings(), called here, before
any test module can start GTK or GSettings), and RETAIN_DATA_DIR points at a temporary
directory, so no test can touch a real collection. Widget tests use tests/gtk.py's
requires_gtk.
"""

import atexit
import importlib.util
import os
import pathlib
import shutil
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / 'src'

if 'retain' not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        'retain', SRC / '__init__.py', submodule_search_locations=[str(SRC)])
    _module = importlib.util.module_from_spec(_spec)
    sys.modules['retain'] = _module
    _spec.loader.exec_module(_module)

if 'RETAIN_DATA_DIR' not in os.environ:
    _data_dir = tempfile.mkdtemp(prefix='retain-test-data-')
    atexit.register(shutil.rmtree, _data_dir, ignore_errors=True)
    os.environ['RETAIN_DATA_DIR'] = _data_dir

from tests.gtk import use_test_settings  # noqa: E402  (needs ROOT, above)

use_test_settings()
