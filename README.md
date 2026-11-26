# podcaster

podcaster turns your notes, PDFs or web pages into a two-host podcast episode,
rendered locally on Apple Silicon.

It assembles the sources into a writing brief, gates the `[S1]`/`[S2]` dialogue script
with a set of checks (including a linter for formulaic AI prose),
and renders it to wav and mp3 with macOS voices, Kokoro or Higgs Audio v2.

It is a command-line tool, `podgen`. Your podcasts live in show folders outside the repo,
so any project, notes folder or agent can call it.

Models download from Hugging Face on first use.
After that, nothing leaves your machine unless you opt in to fetching URLs.

## How it works

1. **Sources** (your files) are combined into a **brief**: the instructions, word budget and
   source text a writer needs to produce one episode.
2. A writer produces the **script**: one `[S1]` or `[S2]` line per turn.
   By default the writer is you or an agent.
3. **Gates** check the script before any audio: structure, formulaic prose, a rubric,
   facts against the sources, free memory, and a first-render safety check.
4. An **engine** renders each turn to audio and stitches the episode into wav and mp3.

A **show** is the folder that holds one podcast's settings, prompts, scripts and renders.
A **mode** picks the kind of episode (`briefing` or `digest`) and which gates apply.

## Requirements

- A Mac with Apple Silicon and macOS 26 or later.
- [uv](https://docs.astral.sh/uv/). podcaster runs on Python 3.12, which uv installs if missing.
- `ffmpeg` on your PATH (`brew install ffmpeg`).
  The `apple` engine needs it for every render. Without it, Kokoro and Higgs still render
  the wav, and the log shows `"mp3": null`.
- Enough memory, which podgen checks in two ways:
  - Installed: 8GB for Kokoro, 24GB for Higgs and for the optional local writer.
  - Free right now: each model's estimate plus 1GB of headroom, which is about 3GB for Kokoro,
    17GB for Higgs and 19GB for the local writer.
  - The `apple` engine runs on any machine and needs about 1.5GB free.
- For the `apple` engine, the system voices "Evan (Enhanced)" and "Zoe (Premium)".
  Download them in System Settings > Accessibility > Spoken Content > System Voice > Manage Voices,
  or change `apple_voice` in your show's `voices/voices.toml`.

## Quickstart

```
git clone https://github.com/thefilesareinthecomputer/podcaster.git && cd podcaster
uv tool install -e .                  # puts podgen on your PATH
podgen render --script src/podcaster/fixtures/short.md --engine apple
podgen render --script src/podcaster/fixtures/short.md --engine kokoro
```

If `podgen` is not found, run `uv tool update-shell` and open a new terminal.
The fixture is a short synthetic script that ships with the package.
Run the renders from the clone, since the fixture path is relative to it.

The first run creates your default show at `~/podcasts/default`.
The wav, mp3, a copy of the script and a JSON render log are written to `~/podcasts/default/out/`.

An engine renders only the bundled fixture until it has completed one render on your machine.
Any other script exits with code 9 until then, and the message prints the command to run.
Kokoro is the default engine, so render the fixture on it once before your first episode.
To bypass the check deliberately, pass `--first-render-ok` to `podgen render`;
`podgen run` does not accept it.
See [Fixture-first](docs/gates.md#fixture-first).

## Shows

A show is a folder holding one podcast's settings and files:

```
~/podcasts/default/
  config.toml          settings: voices per host, speed, gaps, formats, modes, gates
  voices/voices.toml   the voice pool, one entry per voice with its per-engine ids
  prompts/             the writer's text: rules.md, s1.md, s2.md, briefs/<mode>.md
  in/                  source material, if you keep it with the show
  scripts/             dialogue scripts, one file per version
  out/                 renders: wav, mp3, script copy and JSON log
```

`podgen` picks the show in this order: `--show <folder>`, then `$PODGEN_SHOW`,
then `~/podcasts/default`. Only the default show is created automatically.
A show named with `--show` or `$PODGEN_SHOW` must exist first: run `podgen init` on it,
or the command exits with code 1.

```
podgen init ~/podcasts/history        # scaffold a new show from the template
podgen shows ~/podcasts               # list shows
podgen run --show ~/podcasts/history --source ~/notes/history --episode A
```

The code holds nothing specific to a subject or a voice.
A show's prompts, voices and config define the podcast, so editing them makes a different one.

## Writing an episode from sources

```
podgen run --source ~/notes/project --episode A
podgen run --source ~/notes/project paper.pdf --episode A --mode digest
podgen write --source ~/notes/project --episode A    # stop before rendering
```

The default writer is `manual`, which does not write the script for you.
When no script exists yet, `podgen run` prints the brief, the sources and the target path
to stderr, then exits with code 5.
You, or an agent in your session, write the script to that path and rerun the command.

Scripts are saved as `<show>/scripts/<date>-<episode>[-v<n>].md`.
`--episode` is a free label for the file name (default `A`, set in `config.toml`),
and the date is today unless you pass `--date YYYY-MM-DD`.
A rerun looks for the newest script with that date and label, so pass the same `--date`
when you finish a script after midnight.
`--new-version` asks for a fresh version instead of reusing the newest one.

### Sources

- `--source` takes one or more files or folders, anywhere on disk.
- A file you name is read whatever its suffix.
- A folder yields its `.md`, `.txt`, `.pdf`, `.html`, `.htm` and `.url` files in name order,
  skipping symlinks. Add `--recursive` to include subfolders.
- `.url` files hold one link per line.
  They are skipped unless you pass `--fetch`, the only network step after the model download.
- `[sources] max_words` in `config.toml` caps the material with a visible `# truncated` marker.
- The script records where its sources were. In `digest` mode, moving them makes a re-render
  fail the grounding check (exit 7).

### Modes, formats and length

- `--mode` picks the brief, the length strategy and which gates apply:
  `briefing` (default) or `digest`.
  `synthesis` and `study-review` have no brief yet, so `run` and `write` exit with code 10.
- `--format` picks the shape: `deep-dive`, `brief` (one host), `critique` or `debate`.
- `--length short|default|long` scales the word budget by 0.5, 1 or 1.6.
- `--focus "..."` adds one instruction for this episode only.

### Other options

- `--engine` on `run` or `render` overrides the show's default engine (`kokoro`).
- `--writer` on `run` or `write` picks the writer (default `manual`).
- `--config <file>` uses a config file other than the show's `config.toml`.
- `render --mode` overrides the mode in the script's frontmatter.
- `render --out <folder>` writes the render somewhere other than the show's `out/`,
  and `--keep-turns` keeps the per-turn wavs.
- `podgen <command> --help` lists every flag. `podgen` with no command prints help and exits 2.

## Using podgen from another project or agent

An agent working in another repo can produce an episode without knowing this repo's layout:

1. Run `podgen write --source <its notes> --episode <name>`.
   Exit 5 means no script exists yet; stderr carries the brief, the word budget,
   the frontmatter to copy and the exact path to write.
2. Write the script to that path, following the brief.
3. Run `podgen run --source <same notes> --episode <name>`.
   Exit 0 means it rendered; stdout is the JSON render log with the wav, mp3 and script paths.
4. Add `--copy-to <folder>` to also copy the mp3 (the wav without ffmpeg) and its script into
   the caller's own folder.

Use `--show` or `$PODGEN_SHOW` to keep each project's episodes in its own show.
Any other exit code names the reason; see [Gates and exit codes](#gates-and-exit-codes).

## Script format

```
---
date: 2026-09-18
episode: A
mode: digest
format: deep-dive
sources: ~/notes/project/report.md
words: 185
---
# spine: the one question the whole episode answers
# q: an optional outline line, one sub-question each
[S1] First host's line.
[S2] Second host's line.
```

- The frontmatter block is printed by `podgen write`; copy it as given.
  Without a `mode` line, the render uses `[mode].default` from `config.toml` (`briefing`).
  A `digest` script needs `sources`.
- `# spine:` names the one question the episode answers. The rubric gate requires it.
- `# q:` outline lines are optional unless `require_outline = true` in `config.toml`.
- Every other line is one turn: the tag, a space, the line.

`src/podcaster/fixtures/short.md` is a complete example.
Your show's `prompts/rules.md` holds the full rules the writer follows.

## Engines

| engine | what it is | installed | free at render |
|---|---|---|---|
| `apple` | macOS `say` with installed system voices | any | about 1.5GB |
| `kokoro` | Kokoro 82M via mlx-audio, about 300MB on first use | 8GB | about 3GB |
| `higgs` | Higgs Audio v2 3B via mlx-audio, each host's voice cloned from a reference clip | 24GB | about 17GB |

On a machine with less installed memory than the table lists, the engine is refused with exit code 2.
The preflight, a memory check just before audio, exits with code 8 when free memory is too low.

## Voices

Hosts pick a voice from the show's `voices/voices.toml`, which maps one voice id to each engine.

- **apple:** any name from `say -v '?'`, typed exactly, including the parenthetical.
  Premium and Enhanced voices may need downloading in System Settings first.
- **kokoro:** ids like `af_heart`, `af_nicole`, `bf_emma` or `am_adam`.
  The model card grades `af_heart` highest.
- **higgs:** clips do not ship with podcaster. Record a clean mono clip of 5 to 15 seconds
  per host and put it in the show's
  `voices/` folder. In `voices/voices.toml`, `higgs_ref_audio` names the clip
  (`s1.wav`, `s2.wav` by default) and `higgs_ref_text` holds its exact transcript.

Kokoro reads punctuation as pacing.
An ellipsis gives the longest pause, a comma or spaced hyphen gives a short pause,
and markdown symbols are read aloud.

## Gates and exit codes

Six checks run before any audio, in order:
structure, slop, rubric, grounding, memory preflight, then fixture-first.
[docs/gates.md](docs/gates.md) explains each one and its bypass flag.

| code | meaning |
|---|---|
| 0 | rendered |
| 1 | bad script or config |
| 2 | engine unavailable on this machine, or a command-line usage error |
| 3 | slop gate failed |
| 4 | no usable source |
| 5 | no script yet: write it and rerun, or the writer could not produce one |
| 6 | rubric gate failed |
| 7 | grounding gate failed |
| 8 | not enough free memory |
| 9 | engine has not completed a first render |
| 10 | mode not registered or not available |
| 11 | another run holds the machine lock |
| 12 | structure gate failed |

Only one model-loading run happens at a time on a machine.
A second concurrent run exits with code 11 instead of loading another model.

## Output

Renders are named `<date>-<script>-<engine>-<s1 voice>-<s2 voice>-<HHMM>`,
so a re-render never overwrites an earlier one. `podgen render --name` overrides the stem.

Each render saves a byte-for-byte copy of its script beside the audio as `<stem>.md`.
The JSON log records voices, audio and render seconds, the real-time factor,
each gate's result, and script metrics.
[docs/benchmarks.md](docs/benchmarks.md) has measured render times.

## Optional local writer

`uv tool install -e '.[mlx_lm]'` adds the `mlx_lm` writer,
which writes the script on-device with a 4-bit model.
Select it with `--writer mlx_lm`, or set `[writer] default` in `config.toml`.
It needs a 24GB machine with about 19GB free.
It works, but its scripts are not yet good enough to replace `manual`.

## Development

```
uv sync
uv run python -m unittest discover -s tests -v
```

Tests never download a model, render audio, or touch `~/podcasts` or `~/.podcaster`.

| path | what lives there |
|---|---|
| `src/podcaster/cli.py` | the `podgen` command |
| `src/podcaster/pipeline.py` | sources, the brief, the write step |
| `src/podcaster/render.py` | gates and rendering |
| `src/podcaster/paths.py` | where shows, the template and machine state live |
| `src/podcaster/engines/`, `writers/`, `modes/` | the registries |
| `src/podcaster/template/` | what `podgen init` copies into a new show |
| `src/podcaster/vendor/slop_check.py` | the vendored slop linter |

## License

MIT. See [LICENSE](LICENSE).
