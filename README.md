<h1 align="center">podcaster</h1>

<p align="center">
  Turn your notes, PDFs and web pages into a two-host podcast episode, rendered on your Mac.
</p>

<p align="center">
  <img alt="Python 3.12" src="https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white">
  <img alt="Apple Silicon" src="https://img.shields.io/badge/Apple%20Silicon-MLX-000000?logo=apple&logoColor=white">
  <img alt="License: MIT" src="https://img.shields.io/badge/license-MIT-2EA44F">
</p>

<p align="center">
  <a href="#install"><b>Install</b></a> &nbsp;|&nbsp;
  <a href="#your-first-episode"><b>First episode</b></a> &nbsp;|&nbsp;
  <a href="#shows"><b>Shows</b></a> &nbsp;|&nbsp;
  <a href="#calling-it-from-another-project"><b>From another project</b></a> &nbsp;|&nbsp;
  <a href="#troubleshooting"><b>Troubleshooting</b></a> &nbsp;|&nbsp;
  <a href="#reference"><b>Reference</b></a>
</p>

<br>

`podgen` is a command-line tool for Apple Silicon Macs. It does three things:

- `podgen write` turns your source files into a brief: the instructions, word budget and source text a writer needs for one episode.
- `podgen run` checks the finished script and renders it to wav and mp3 with two distinct voices.
- `podgen render` renders a script you already have.

You, or an AI agent in your session, write the script from the brief. Before any audio, a set of gates checks it: its structure, formulaic AI prose, a rubric, its facts against the sources, and free memory.

It renders with macOS voices, [Kokoro](https://huggingface.co/hexgrad/Kokoro-82M), or [Higgs Audio v2](https://huggingface.co/bosonai/higgs-tts-2-3b-base) with cloned voices, through [mlx-audio](https://github.com/Blaizzy/mlx-audio). Models download from Hugging Face once. After that, nothing leaves your Mac unless you ask it to fetch a URL.

It runs on an M1 MacBook Air with 16 GB of memory, where Kokoro renders a 30-minute episode in under four minutes.

## Install

You need:

- A Mac with Apple Silicon and macOS 26 or later.
- [uv](https://docs.astral.sh/uv/). Install it with `brew install uv`. It installs Python 3.12 if you don't have it.
- `ffmpeg`, for mp3 output and for the macOS voices. Install it with `brew install ffmpeg`.

Then run:

```bash
git clone https://github.com/thefilesareinthecomputer/podcaster.git
cd podcaster
uv tool install -e .
```

Check it with a first render of the bundled sample script:

```bash
podgen render --script src/podcaster/fixtures/short.md --engine kokoro
```

1. The first run creates your default show at `~/podcasts/default`. See [Shows](#shows).
2. It downloads Kokoro, about 390 MB, once.
3. The wav, mp3, a copy of the script and a JSON log are written to `~/podcasts/default/out/`.

Each engine renders only this sample until it has finished one render on your Mac, so a new engine is never first tried on a full episode. Kokoro is the default engine, which is why the check uses it.

## Updating

From the `podcaster` folder you cloned, run:

```bash
git pull
uv tool install -e . --reinstall
```

`git pull` brings in the new code, which the editable install uses right away. The reinstall picks up any changed dependencies. Your shows are outside the repo, so an update never touches them.

## Your first episode

```bash
podgen write --source ~/notes/project --episode A
```

1. `podgen write` reads your sources and prints the brief to the terminal, ending with the exact path to write the script to. It exits with code 5, which means the script doesn't exist yet.
2. Write the script to that path, by hand or with an AI agent, following the brief.
3. Render it:

```bash
podgen run --source ~/notes/project --episode A
```

If a gate refuses the script, the message names the turn or line and the problem. Fix the script and run the same command again.

- **Sources** can be files or folders, anywhere on disk: `.md`, `.txt`, `.pdf`, `.html` and `.url` (a list of links, fetched only with `--fetch`).
- **Episode names** are free labels used in file names. Scripts are saved as `<show>/scripts/<date>-<episode>.md`, dated today unless you pass `--date YYYY-MM-DD`. Pass the same `--date` if you finish a script after midnight.
- **Modes** pick the kind of episode. `briefing`, the default, is a two-host conversation about your material. `digest` summarizes documents, and its gate checks every number and name against them.
- **Shape and length.** `--format` picks `deep-dive` (default), `brief` (one host), `critique` or `debate`. `--length short` or `long` scales the word budget by 0.5 or 1.6. `--focus "..."` adds one instruction for this episode.

<details>
<summary><b>What a script looks like</b></summary>

<br>

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
[S1] First host's line.
[S2] Second host's line.
```

- `podgen write` prints the frontmatter block. Copy it as given.
- `# spine:` names the question the episode answers. The rubric gate requires it.
- Every other line is one turn: the speaker tag, a space, then the line.
- `src/podcaster/fixtures/short.md` is a complete example, and your show's `prompts/rules.md` has the full writing rules.

</details>

## Shows

A show is a folder that holds one podcast: its settings, voices, prompts, scripts and renders. podcaster's own repo holds only code, so you can update it without touching your podcasts.

```
~/podcasts/default/
  config.toml          voices per host, speed, pauses, formats, modes, gates
  voices/voices.toml   the voices, each with its id for every engine
  prompts/             the writing rules, one persona per host, one brief per mode
  scripts/             your scripts
  out/                 renders: wav, mp3, script copy and JSON log
```

```bash
podgen init ~/podcasts/history      # make a new show from the template
podgen shows ~/podcasts             # list your shows
podgen run --show ~/podcasts/history --source ~/notes/history --episode A
```

`podgen` uses `--show` first, then `$PODGEN_SHOW`, then `~/podcasts/default`. Only the default show is created for you; run `podgen init` before using any other.

To make a different podcast, edit a show's prompts, voices and config. The code knows nothing about any subject or voice.

## Calling it from another project

An AI agent working in another repository can make an episode from its own notes:

1. `podgen write --source <notes> --episode <name>` exits with code 5 and prints the brief, the word budget, the frontmatter and the path to write.
2. The agent writes the script to that path.
3. `podgen run --source <notes> --episode <name>` exits with code 0 when it renders, and prints the JSON log, with the audio and script paths, to stdout.
4. `--copy-to <folder>` also copies the mp3 and its script into the agent's own folder.

Give each project its own show with `--show` or `$PODGEN_SHOW`. Any other exit code names the reason; see [Exit codes](#exit-codes).

## Troubleshooting

- **`command not found: podgen`.** Run `uv tool update-shell`, then open a new terminal window.
- **Exit code 9, "no successful render recorded".** That engine hasn't rendered on this Mac yet. Run the command the message prints, which renders the sample script, then try again.
- **Exit code 8: free memory is too low.** Close some apps, or use a lighter engine with `--engine apple`.
- **Exit code 2, engine unavailable.** Your Mac has less memory than the engine needs. Higgs needs a 24 GB Mac.
- **The macOS voices fail.** The default voices, "Evan (Enhanced)" and "Zoe (Premium)", need downloading first: **System Settings > Accessibility > Spoken Content > System Voice > Manage Voices**. Or set `apple_voice` in your show's `voices/voices.toml`.
- **No mp3, and the log shows `"mp3": null`.** `ffmpeg` is missing. Install it with `brew install ffmpeg`.
- **Exit code 1, "bad config", with `--show`.** That show doesn't exist yet. Run `podgen init` on the folder first.

## Reference

### Engines

| Engine | What it is | Mac size | Free memory | Download |
|---|---|---|---|---|
| `apple` | macOS `say` with your installed voices | any | about 1.5 GB | none |
| `kokoro` | [`mlx-community/Kokoro-82M-bf16`](https://huggingface.co/mlx-community/Kokoro-82M-bf16), the default | 8 GB | about 3 GB | about 390 MB |
| `higgs` | [`mlx-community/higgs-audio-v2-3B-mlx-q8`](https://huggingface.co/mlx-community/higgs-audio-v2-3B-mlx-q8), one cloned voice per host | 24 GB | about 17 GB | about 6.2 GB |

- Pick an engine with `--engine`, or set `engine_default` in your show's `config.toml`.
- Kokoro voices are ids such as `af_heart`, `af_nicole`, `bf_emma` or `am_adam`. The first letter is the accent (`a` American, `b` British), the second is `f` female or `m` male.
- Higgs clones each host from a clean mono clip of 5 to 15 seconds that you record. Put the clip in the show's `voices/` folder, then set `higgs_ref_audio` to its file name and `higgs_ref_text` to its exact transcript in `voices/voices.toml`.
- Kokoro reads punctuation as pacing. An ellipsis is the longest pause, a comma or spaced hyphen a short one, and markdown symbols are read aloud.

[docs/benchmarks.md](docs/benchmarks.md) has measured render times.

### Exit codes

| Code | Meaning |
|---|---|
| 0 | rendered |
| 1 | bad script or config |
| 2 | engine unavailable on this Mac, or a command-line usage error |
| 3 | slop gate failed: formulaic AI prose |
| 4 | no usable source |
| 5 | no script yet: write it and rerun, or the writer could not produce one |
| 6 | rubric gate failed |
| 7 | grounding gate failed: a fact is not in the sources |
| 8 | not enough free memory |
| 9 | this engine has not finished a first render on this Mac |
| 10 | the mode cannot run (`synthesis` and `study-review` have no brief yet) |
| 11 | another podgen run is using the machine |
| 12 | structure gate failed |

The gates run in this order: structure, slop, rubric, grounding, memory, first render. [docs/gates.md](docs/gates.md) explains each one and its bypass flag, such as `--allow-slop` for a verbatim quote.

### Options

| Option | Commands | What it does |
|---|---|---|
| `--source <paths>` | `write`, `run` | files or folders to read; `--recursive` includes subfolders |
| `--episode <name>` | `write`, `run` | the episode label, default `A` |
| `--date YYYY-MM-DD` | `write`, `run` | the script's date, default today |
| `--new-version` | `write`, `run` | write a fresh version instead of reusing the newest script |
| `--mode`, `--format`, `--length`, `--focus` | `write`, `run` | the kind, shape and length of the episode |
| `--fetch` | `write`, `run` | allow `.url` sources to be fetched |
| `--writer <name>` | `write`, `run` | `manual` (default) or `mlx_lm` |
| `--engine <name>` | `run`, `render` | `apple`, `kokoro` or `higgs` |
| `--show <folder>` | `write`, `run`, `render` | the show to use |
| `--copy-to <folder>` | `run`, `render` | also copy the audio and script here |
| `--out <folder>` | `render` | write the render here instead of the show's `out/` |
| `--name <stem>` | `render` | the output file name, without extension |
| `--keep-turns` | `render` | keep the per-turn wavs |
| `--first-render-ok` | `render` | skip the first-render check for this engine |

`podgen <command> --help` lists every option.

### Memory

Only one `podgen` run uses the machine at a time; a second one exits with code 11 instead of loading another model. Before any audio, `podgen` checks that the engine's estimated memory plus 1 GB is free, and stops with code 8 if it isn't. Each turn streams to disk, so a long episode uses no more memory than a short one.

### Privacy

- **Network:** after the models download, `podgen` makes no network requests unless you pass `--fetch`.
- **Your material:** sources, scripts and renders stay in your show folder. Each render's JSON log records the source paths.
- **The writer:** the default `manual` writer sends nothing anywhere. If an AI agent writes your script, the brief goes wherever that agent sends its messages.

### Local writer (experimental)

`uv tool install -e '.[mlx_lm]' --reinstall` adds `mlx_lm`, which writes the script on your Mac with a 4-bit model. Select it with `--writer mlx_lm`. It needs a 24 GB Mac with about 19 GB free, and its scripts are not yet good enough to replace writing them yourself.

### Development

```bash
uv sync
uv run python -m unittest discover -s tests -v
```

The tests never download a model, render audio, or touch `~/podcasts` or `~/.podcaster`. The code is in `src/podcaster/`: `cli.py` is the command, `pipeline.py` writes, `render.py` gates and renders, and `paths.py` decides where everything lives. Engines, writers and modes each have a registry folder, and `template/` is what `podgen init` copies.

## Contributing

See [AGENTS.md](AGENTS.md) for how the code is organized and what a change needs.

## License

The code is [MIT](LICENSE). The models download separately and keep their own licenses: Kokoro is Apache-2.0. The Higgs MLX port is tagged Apache-2.0, but the original Higgs Audio v2 model lists its own license, so read [Boson AI's terms](https://huggingface.co/bosonai/higgs-tts-2-3b-base) before any commercial use.
