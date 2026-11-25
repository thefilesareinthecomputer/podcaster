# Gates

Every render runs these checks in order before any audio is produced.
Each has its own exit code and its own bypass flag.
A bypass is recorded in the render's JSON log.

| # | gate | checks | exit | bypass |
|---|---|---|---|---|
| 1 | structure | hosts, turn share, turn length, banned artifacts | 12 | `--allow-structure-fail` |
| 2 | slop | formulaic AI prose | 3 | `--allow-slop` |
| 3 | rubric | spine, turn length band, listener address | 6 | `--allow-rubric-fail` |
| 4 | grounding | facts in the script appear in the sources | 7 | `--allow-ungrounded` |
| 5 | memory preflight | enough free memory for the engine | 8 | none |
| 6 | fixture-first | the engine has completed one render here | 9 | `--first-render-ok` |

## Structure

Settings live in `[gate]` in `config.toml`.

- Every host of the chosen format must speak.
  Two-host formats need at least 30 percent of turns per host (`min_turn_share`).
  A one-host format fails on any `[S2]` turn.
- No turn may run over 120 words (`max_turn_words`).
- `# q:` outline lines are required only when `require_outline = true`.
  The shipped config sets it to `false`.

Failures print with turn numbers.

## Slop

`src/podcaster/vendor/slop_check.py` lints every script.

- Non-keyboard characters, filler and the other mechanical classes fail.
- "X, not Y" antitheses fail above `max(2, words / 400)` per script.
- Handwave words warn but do not fail.

`--allow-slop` is meant for a verbatim quote. State the reason when you use it.

The linter is a single stdlib-only file vendored from the author's
ai-slop-magic-eraser skill, MIT licensed. It ships in the package and is not configurable,
so a show's config cannot name a different file for the gate to run.
To check a script before rendering, run `python src/podcaster/vendor/slop_check.py <script>`.
To refresh it, copy the upstream file over it and restore the attribution comment lines.
Do not edit it in place.

## Rubric

`src/podcaster/script_rubric.py` scores three axes.

- `spine`: a `# spine:` comment line between the frontmatter and the first turn.
- `turn_length`: every turn falls inside the 20 to 45 word band.
- `address`: no sentence calls the listener "you" and refers to them in the third person at once.

Each mode weights the axes, and a weight of 0.0 skips that axis.

| mode | spine | turn_length | address |
|---|---|---|---|
| `briefing` | yes | yes | yes |
| `digest` | yes | yes | no |
| `synthesis` | yes | yes | no |
| `study-review` | yes | no | no |

The mode comes from the script's `mode:` frontmatter.
`--mode` on the render command overrides it,
and a script with neither uses `[mode].default` from `config.toml`.

## Grounding

`src/podcaster/grounding_check.py` runs only for modes with a grounding strategy:

- `digest` uses `any_source`.
- `synthesis` uses `named_source`, which is designed but not built,
  so a synthesis script exits 10 instead of rendering unchecked.
- `briefing` and `study-review` skip this gate.

The gate reopens the files in the script's `sources:` frontmatter
and flags any number, acronym or proper name that appears in none of them.
It cannot catch a real fact that has been mischaracterized:
a source saying "80 percent" and a script saying "nearly all" passes.

A grounded mode with no `sources:` line, or one naming a missing file, also exits 7.

## Memory preflight

`render.py` reads free memory with `vm_stat` and compares it to the engine's
estimated resident size plus a small audio buffer and `preflight_headroom_gb`.
It prints the estimate and the measurement to stderr either way.

The audio term stays small because each turn streams to the output wav
instead of the whole episode being held in memory.

## Fixture-first

Until an engine has completed one render on this machine,
it renders only the packaged fixture, `src/podcaster/fixtures/short.md`.
The refusal message prints the exact command to render it.

A completed render writes `~/.podcaster/first-render-ok/<engine>` with the date and the script.
The marker is per engine and shared by every show on the machine,
so a Kokoro success does not clear Higgs.
Delete the marker to put an engine back behind the gate.
