"""Shared test setup: a throwaway show and marker folder, so no test touches the real ones.

Importing this module points $PODGEN_SHOW at a temporary show scaffolded from the packaged
template, and the first-render markers at a temporary folder. unittest discovery imports
every test module before running any test, so one import anywhere covers the whole run;
each module that drives a main() imports it anyway, so it holds when run on its own.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

from podcaster import paths, render

TEMPLATE = paths.TEMPLATE
FIXTURE = paths.FIXTURE
FIXTURES = Path(__file__).resolve().parent / "fixtures"

_tmp = Path(tempfile.mkdtemp(prefix="podgen-test-"))
atexit.register(shutil.rmtree, _tmp, ignore_errors=True)

SHOW = _tmp / "show"
paths.init_show(SHOW)
os.environ[paths.SHOW_ENV] = str(SHOW)

# A grounded render only opens sources under the show or under home, and a checkout can sit
# anywhere, so the test source lives inside the test show.
SOURCE = SHOW / "in" / "short-source.md"
shutil.copy(FIXTURES / "short-source.md", SOURCE)

MARKERS = _tmp / "markers"
paths.FIRST_RENDER_DIR = MARKERS
render.FIRST_RENDER_DIR = MARKERS


@contextmanager
def show_at(root: Path | None = None):
    """Make `root`, or a fresh folder, a show scaffolded from the template and the default show.

    Yields the show's scripts folder, resolved, which is where the pipeline looks for and names
    scripts.
    """
    root = Path(tempfile.mkdtemp(dir=_tmp)) if root is None else root
    paths.init_show(root)
    with mock.patch.dict(os.environ, {paths.SHOW_ENV: str(root)}):
        yield (root / "scripts").resolve()
