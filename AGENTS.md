# Agent and contributor guide

`podcaster` is a local two-host podcast generator for Apple Silicon. It takes source text,
writes or receives a `[S1]`/`[S2]` dialogue script, gates the script, and renders it to wav
and mp3 with built-in or open-weight voices. Usage is in the README.

If a local, untracked `tasks/` folder exists, read `tasks/todo.md` and `tasks/machines.md`
first. They hold the current state and machine-specific notes.

## Core rule: generic engine, customized at the edges

The package under `src/podcaster/` knows nothing about any subject, person, voice or episode.
Everything that makes one podcast differ from another lives in a show folder outside the repo:

- `config.toml`: voices per host, speeds, gaps, model ids, budgets, formats, modes, gates.
- `voices/voices.toml`: the voice pool.
- `prompts/`: shared rules, one persona per host, one brief per mode.
- `in/`, `scripts/`, `out/`: sources, scripts and renders.

Test for every change: could someone run `podgen init ~/podcasts/theirs`, edit those files,
and get their own podcast without touching a `.py` file? If not, the change is in the wrong
layer. No subject names, personal references, voice ids or prompt text in code.

## Working rules

- Python 3.12 via uv, project-local `.venv`.
  `uv sync`, then `uv run python -m unittest discover -s tests -v`.
- Tests never download a model, render audio, or touch `~/podcasts` or `~/.podcaster`.
  `tests/support.py` points the run at a temporary show and marker folder;
  any test that drives a `main()` imports it.
- `src/podcaster/paths.py` owns every location: the packaged template, fixture and linter,
  machine state under `~/.podcaster`, and show resolution
  (`--show`, then `$PODGEN_SHOW`, then `~/podcasts/default`).
- Engines and writers live in memory-gated registries (`engines/`, `writers/`), modes in
  `modes/`. New ones register there, never inside `pipeline.py` or `render.py`.
- Relative paths in a show's config resolve against the show folder and are confined to it;
  only `sources:` may also point anywhere under home. Model ids must be in `mlx-community/`.
  The slop linter and the fixture are not configurable.
- A script's `sources:` frontmatter is written `~/...` for anything under home and absolute
  otherwise. Never write a path relative to the working directory.
- Six checks run before audio, each with its own exit code: structure (12), slop (3),
  rubric (6), grounding (7), memory preflight (8), fixture-first (9).
  `--allow-slop` is for a verbatim quote, with the reason stated.
- One model-loading run at a time per machine, enforced by `guard.py` with a lock at
  `~/.podcaster.lock` (exit 11). Agents: run `pgrep -fl "podgen|mlx_lm"` before starting a
  render, never start a second one, and never background a model load you will not watch.
- Output names carry the date, script, engine, both voices and a time; nothing overwrites.
- Examples, fixtures and tests use synthetic material only. The shipped examples describe
  a fictional volunteer weather station.
- Prose in docs, prompts, scripts and commits: no em dashes, no emoji, American spelling.

## Ask first

- Any dependency beyond mlx-audio, pypdf, trafilatura and the optional mlx-lm.
- Changing the `[S1]`/`[S2]` tag format.
- Adding an RSS, upload or hosting step.
- Pulling a model outside the Hugging Face `mlx-community` namespace.
