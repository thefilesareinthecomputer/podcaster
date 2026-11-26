"""Where podcaster's files live: what ships in the package, what the machine keeps, and the show.

The package carries a show template (config, voice pool, generic prompts), the fixture script
and the vendored slop linter. Everything a podcast owns lives in a show folder outside the
package: config.toml, in/, prompts/, voices/, scripts/ and out/. Machine state, the per-engine
first-render markers, lives under ~/.podcaster so every show on the machine shares it.

The show is --show when given, else $PODGEN_SHOW, else ~/podcasts/default, which is created
from the template the first time it is needed.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
TEMPLATE = PACKAGE / "template"
FIXTURE = PACKAGE / "fixtures" / "short.md"
SLOP_CHECK = PACKAGE / "vendor" / "slop_check.py"

STATE = Path.home() / ".podcaster"
FIRST_RENDER_DIR = STATE / "first-render-ok"

SHOW_ENV = "PODGEN_SHOW"
DEFAULT_SHOW = Path.home() / "podcasts" / "default"
SHOW_DIRS = ("in", "prompts", "prompts/briefs", "voices", "scripts", "out")
PROMPT_FILES = ("rules.md", "s1.md", "s2.md")


def show_root(show: Path | None = None) -> Path:
    """The show folder: --show, else $PODGEN_SHOW, else ~/podcasts/default."""
    if show:
        return Path(show).expanduser()
    env = os.environ.get(SHOW_ENV)
    return Path(env).expanduser() if env else DEFAULT_SHOW


def init_show(target: Path, template: Path = TEMPLATE) -> list[str]:
    """Create a show folder. Copies config.toml, the voice pool and the prompts. Never overwrites."""
    target.mkdir(parents=True, exist_ok=True)
    made: list[str] = []
    for d in SHOW_DIRS:
        (target / d).mkdir(parents=True, exist_ok=True)
    copies = ["config.toml", "voices/voices.toml"]
    copies += [f"prompts/{name}" for name in PROMPT_FILES]
    copies += [f"prompts/briefs/{p.name}" for p in sorted((template / "prompts" / "briefs").glob("*.md"))]
    for rel in copies:
        src, dst = template / rel, target / rel
        # is_symlink() too: a dangling link reports exists() False and copy2 would write through it.
        if src.exists() and not dst.exists() and not dst.is_symlink():
            shutil.copy2(src, dst)
            made.append(rel)
    missing = [f"prompts/{n}" for n in PROMPT_FILES if not (target / "prompts" / n).exists()]
    return made + [f"MISSING {m}" for m in missing]


def show_config(show: Path | None = None) -> Path:
    """The config.toml of the resolved show. The default show is created on first use.

    A show named with --show or $PODGEN_SHOW is never created here, so a typo in a path
    fails loudly instead of scaffolding a new show somewhere unexpected.
    """
    root = show_root(show)
    config = root / "config.toml"
    if not config.exists() and root == DEFAULT_SHOW and not show and SHOW_ENV not in os.environ:
        init_show(root)
        print(f"created the default show at {root} from the template", file=sys.stderr)
    return config
