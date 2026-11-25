#!/usr/bin/env python3
"""Render a two-host script to audio.

    podgen render --script scripts/2026-09-17-A.md --engine apple
    podgen render --script scripts/2026-09-17-A.md --engine kokoro --copy-to ~/some/folder

Exit codes: 0 ok, 1 bad script or bad config, 2 engine unavailable on this machine,
3 slop gate failed, 6 rubric gate failed, 7 grounding gate failed, 8 not enough free memory
for this render, 9 this engine has no recorded successful render yet, 10 the mode cannot run
(message "mode unavailable:"), 11 another run holds the machine lock, 12 structure gate failed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
import tomllib
import wave
from pathlib import Path

from podcaster import engines, grounding_check, guard, modes, paths, script_rubric
from podcaster.script_format import (
    ScriptError,
    gap_plan,
    metrics,
    outline,
    parse_script,
    section_starts,
    sources_from_meta,
    strip_frontmatter,
    structure_check,
    word_count,
)

DATED = re.compile(r"^\d{4}-\d{2}-\d{2}-")  # script stems that already carry their date

# Config values naming a file the show owns. Each resolves against the config's own folder,
# so a show names its own prompts and voices without absolute paths. The fixture and the
# slop linter are not config keys at all: they ship in the package (see paths.py).
PATH_KEYS = (
    ("writer", "rules"),
    ("writer", "s1"),
    ("writer", "s2"),
)


def confined(value: str, key: str, *roots: Path) -> str:
    """Resolve `value` and refuse it unless it lands under one of `roots`. First root joins.

    `Path("/a") / "/etc/passwd"` is `/etc/passwd`: pathlib drops the left operand when the
    right is absolute, so a plain join silently honors whatever a config names. That matters
    because --show and --config exist so a config can arrive with a show folder the user did
    not write.

    Absolute and `..` are allowed as spellings; where they land is what decides. A show's own
    assets are confined to the show root and home, the two places material lives.
    """
    candidate = Path(value).expanduser()
    resolved = (candidate if candidate.is_absolute() else roots[0] / candidate).resolve()
    if not any(resolved.is_relative_to(r.resolve()) for r in roots):
        where = " or ".join(str(r) for r in roots)
        raise KeyError(f"{key} = {value!r} resolves to {resolved}, outside {where}")
    return str(resolved)


def load_config(path: Path) -> dict:
    """Load a show's config. Relative paths in it resolve against the config's own folder.

    That folder is the show root: where in/, scripts/, out/, prompts/ and voices/ live. It is
    stored under `_root`, and every later path lookup goes through it. Without --show or
    --config the show is $PODGEN_SHOW or ~/podcasts/default; see paths.py.

    Every path key is confined to the root it belongs to, so a config cannot name a file
    outside its own folder. See confined().
    """
    path = Path(path).resolve()
    with open(path, "rb") as fh:
        cfg = tomllib.load(fh)
    root = path.parent
    for table, key in PATH_KEYS:
        if table in cfg and key in cfg[table]:
            cfg[table][key] = confined(cfg[table][key], f"{table}.{key}", root, Path.home())
    for name, mode_cfg in cfg.get("modes", {}).items():
        if "brief" in mode_cfg:
            mode_cfg["brief"] = confined(mode_cfg["brief"], f"modes.{name}.brief", root, Path.home())
    cfg["voices"] = load_voice_pool(root / "voices" / "voices.toml")
    for name, host in cfg.get("hosts", {}).items():
        if "voice" in host:
            if host["voice"] not in cfg["voices"]:
                raise KeyError(
                    f"hosts.{name}.voice = {host['voice']!r} is not in voices/voices.toml; "
                    f"known: {', '.join(cfg['voices']) or 'none'}"
                )
            # The pool entry supplies every per-engine key; anything the host sets itself wins.
            merged = dict(cfg["voices"][host["voice"]])
            merged.pop("description", None)
            merged.update(host)
            host.clear()
            host.update(merged)
        if "higgs_ref_audio" in host:
            host["higgs_ref_audio"] = confined(
                host["higgs_ref_audio"], f"hosts.{name}.higgs_ref_audio", root, Path.home()
            )
    cfg["_root"] = str(root)
    return cfg


def resolve_source_paths(meta: dict, root: Path) -> list[Path]:
    """The script's `sources` frontmatter as paths this machine can open.

    Three spellings, all valid, because a script does not always stay where it was written:

    - `~/notes/a.md` expands to this user's home. This is the portable one, and it is what
      pipeline.py writes for anything under home: the same person on two Macs has two
      usernames, so a raw `/Users/<name>/...` is a path only one of the two can open.
    - an absolute path is used as given, for a source that lives outside home.
    - anything else resolves against the show root, which is what a committed script wants:
      a fixture naming `/Users/<name>/...` fails the grounding gate on any other machine,
      even with the source file sitting next to it.
    """
    resolved = []
    for p in sources_from_meta(meta):
        p = p.expanduser()
        resolved.append((p if p.is_absolute() else root / p).resolve())
    return resolved


def readable_sources(meta: dict, root: Path) -> tuple[list[Path], list[Path]]:
    """Split a script's sources into the ones this run will open and the ones it refuses.

    Sources are confined to the show root or to home, which is where every real source lives
    on these machines. Anything else is named and skipped rather than opened. The confinement
    exists because the `manual` writer's frontmatter is written by whoever answered a write
    instruction that quotes the source material verbatim, so a `sources:` line does not by
    itself mean the user chose that path.
    """
    allowed_roots = [root.resolve(), Path.home().resolve()]
    ok: list[Path] = []
    refused: list[Path] = []
    for p in resolve_source_paths(meta, root):
        (ok if any(p.is_relative_to(a) for a in allowed_roots) else refused).append(p)
    return ok, refused


def load_voice_pool(path: Path) -> dict[str, dict]:
    """voices/voices.toml: one table per voice id with a description and per-engine keys.

    Clip paths inside it are relative to voices/, so a pool entry moves with the folder.
    A show with no pool file is fine: hosts then name their per-engine voice ids directly.
    """
    if not path.exists():
        return {}
    with open(path, "rb") as fh:
        pool = tomllib.load(fh)
    for entry in pool.values():
        if "higgs_ref_audio" in entry:
            entry["higgs_ref_audio"] = str(Path("voices") / entry["higgs_ref_audio"])
    return pool


def stitch(turn_wavs: list[Path], out_wav: Path, gap_ms: int | list[int]) -> float:
    """Concatenate mono 16-bit wavs with silence between turns. Returns audio seconds.

    `gap_ms` is either one figure for every boundary or one per boundary, which is what
    `script_format.gap_plan()` produces: shorter after a question, longer at a section or a
    closing turn.

    Every turn is validated from its header first, so a mismatch raises before the output file
    is opened and leaves nothing partial on disk. Frames are then streamed a second at a time,
    so peak memory holds one chunk rather than the whole episode. Buffering the whole episode
    has locked a 16GB machine; the streaming body is not an optimization to trade away.
    """
    rate = None
    frame_counts = []
    for p in turn_wavs:
        with wave.open(str(p), "rb") as wf:
            if rate is None:
                rate = wf.getframerate()
            elif wf.getframerate() != rate:
                raise RuntimeError(f"sample rate mismatch in {p.name}: {wf.getframerate()} vs {rate}")
            if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                raise RuntimeError(f"{p.name} is not 16-bit mono")
            frame_counts.append(wf.getnframes())
    assert rate is not None
    boundaries = len(turn_wavs) - 1
    plan = [gap_ms] * boundaries if isinstance(gap_ms, int) else list(gap_ms)
    if len(plan) != boundaries:
        raise RuntimeError(f"gap plan has {len(plan)} entries for {len(turn_wavs)} turns")
    gap_frames = [int(rate * ms / 1000) for ms in plan]
    with wave.open(str(out_wav), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        for i, p in enumerate(turn_wavs):
            if i:
                out.writeframes(b"\x00\x00" * gap_frames[i - 1])
            with wave.open(str(p), "rb") as wf:
                while True:
                    chunk = wf.readframes(rate)
                    if not chunk:
                        break
                    out.writeframes(chunk)
                    del chunk
    return (sum(frame_counts) + sum(gap_frames)) / rate


GIB = 1024**3
VM_STAT_FREE_KEYS = ("Pages free", "Pages inactive", "Pages speculative")


def parse_vm_stat(text: str) -> int:
    """Return reclaimable free bytes from `vm_stat` output: free, inactive and speculative pages."""
    page_size = None
    first = text.splitlines()[0] if text else ""
    if "page size of" in first:
        page_size = int(first.split("page size of")[1].split("bytes")[0].strip())
    if page_size is None:
        raise RuntimeError("vm_stat output has no page size in its header line")
    pages = 0
    for line in text.splitlines()[1:]:
        key, _, value = line.partition(":")
        if key.strip() in VM_STAT_FREE_KEYS:
            pages += int(value.strip().rstrip("."))
    return pages * page_size


def free_memory_bytes() -> int:
    """Free memory right now, measured with `vm_stat`."""
    out = subprocess.run([guard.VM_STAT], capture_output=True, text=True, check=True)
    return parse_vm_stat(out.stdout)


def estimate_render_bytes(
    spec: engines.EngineSpec | list[engines.EngineSpec],
    words: int,
    sample_rate: int,
    words_per_minute: int,
    headroom_gb: float,
) -> dict[str, int]:
    """Estimated peak bytes for one render: engine residency, audio buffers, headroom.

    `spec` is one engine or every engine this render loads. A per-host engine override holds
    both models at once, so their resident terms add; asking only for the named engine is how
    a two-engine render ends up swapping.

    stitch() streams each turn to disk a second at a time, so the audio term is a small
    constant, two one-second chunks (the read chunk and the gap, which is at most that), and
    does not grow with episode length. `words` and `words_per_minute` stay in the signature
    for future per-engine terms.
    """
    specs = [spec] if isinstance(spec, engines.EngineSpec) else list(spec)
    resident = int(sum(s.resident_gb for s in specs) * GIB)
    audio = sample_rate * 2 * 2
    headroom = int(headroom_gb * GIB)
    return {"resident": resident, "audio": audio, "headroom": headroom, "total": resident + audio + headroom}


def _gb(n: int) -> str:
    return f"{n / GIB:.1f}GB"


def preflight(
    spec: engines.EngineSpec | list[engines.EngineSpec], words: int, cfg: dict
) -> tuple[bool, str]:
    """Compare free memory against the estimated need for this render. Return (ok, report)."""
    specs = [spec] if isinstance(spec, engines.EngineSpec) else list(spec)
    rcfg = cfg["render"]
    need = estimate_render_bytes(
        specs,
        words,
        rcfg["sample_rate"],
        cfg["episode"]["words_per_minute"],
        rcfg.get("preflight_headroom_gb", 1.0),
    )
    free = free_memory_bytes()
    ok = free >= need["total"]
    named = " + ".join(s.name for s in specs)
    parts = f"{_gb(need['resident'])} {named} resident + {_gb(need['audio'])} audio + {_gb(need['headroom'])} headroom"
    report = (
        f"memory preflight: needs about {_gb(need['total'])} ({parts}); "
        f"{_gb(free)} free measured"
    )
    if not ok:
        report += "\nnot enough free memory for this render; close other apps or pick a lighter engine"
    return ok, report


FIRST_RENDER_DIR = paths.FIRST_RENDER_DIR
FIXTURE = paths.FIXTURE


def first_render_marker(engine_name: str, marker_dir: Path = FIRST_RENDER_DIR) -> Path:
    """Where this engine's proof of a completed render lives. One file per engine, per machine."""
    return marker_dir / engine_name


def is_fixture(script: Path) -> bool:
    """True when `script` is the packaged fixture, whatever path spelling was used."""
    return script.resolve() == FIXTURE.resolve()


def fixture_gate(
    script: Path,
    engine_name: str,
    override: bool,
    marker_dir: Path = FIRST_RENDER_DIR,
) -> tuple[bool, str]:
    """Hold a full-length script back until this engine has finished one render. Return (ok, report).

    The fixture always passes, and so does any script once the engine has a marker. A first
    full-length render on an unproven engine is the case that has locked a machine before,
    which is why the marker is per engine rather than per machine.
    """
    if is_fixture(script):
        return True, "fixture-first gate: this is the fixture script, always allowed"
    marker = first_render_marker(engine_name, marker_dir)
    if marker.exists():
        return True, f"fixture-first gate: {engine_name} has a recorded render at {marker}"
    if override:
        return True, f"fixture-first gate: bypassed with --first-render-ok, no marker at {marker}"
    report = (
        f"fixture-first gate: no successful render recorded for {engine_name}; "
        f"no marker at {marker}\n"
        f"a first full-length render on an unproven engine can exhaust memory and lock the machine\n"
        f"render the fixture first: podgen render --script {FIXTURE} --engine {engine_name}\n"
        f"or pass --first-render-ok to take the risk deliberately"
    )
    return False, report


SLOP_CHECK = paths.SLOP_CHECK
HARD_CATEGORIES = {"symbol", "filler", "sycophancy", "meta", "hedge", "emoji"}
CADENCE_WORDS_PER_HIT = 400  # more than one antithesis per 400 words fails


def slop_gate(script: Path, words: int, checker: Path = SLOP_CHECK) -> tuple[bool, str]:
    """Run the slop linter. Return (ok, report). Hard categories and cadence density fail; handwave only warns.

    A missing linter is a supported state, not an error: the gate skips with a message and the
    render continues. The linter ships vendored in the package at vendor/slop_check.py. It is
    not configurable: a show's config could otherwise name a Python file for the gate to run.
    """
    if not checker.exists():
        return True, (
            f"slop gate skipped: no linter at {checker}; "
            "the render proceeds without the mechanical slop check"
        )
    proc = subprocess.run([sys.executable, str(checker), "--json", str(script)], capture_output=True, text=True)
    try:
        findings = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        return False, f"slop gate: could not parse linter output\n{proc.stdout}{proc.stderr}"
    hard = [f for f in findings if f.get("category") in HARD_CATEGORIES]
    cadence = [f for f in findings if f.get("category") == "cadence"]
    other = [f for f in findings if f not in hard and f not in cadence]
    cadence_limit = max(2, words // CADENCE_WORDS_PER_HIT)
    lines = [f"slop gate: {len(findings)} finding(s); cadence {len(cadence)} (limit {cadence_limit}), hard {len(hard)}, warn {len(other)}"]
    for f in hard + cadence + other:
        lines.append(f"  {f.get('line')}:{f.get('col')} [{f.get('category')}] {f.get('matched')!r} {f.get('message')}")
    ok = not hard and len(cadence) <= cadence_limit
    return ok, "\n".join(lines)


def to_mp3(wav: Path, mp3: Path, bitrate: str) -> Path | None:
    if shutil.which("ffmpeg") is None:
        return None
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(wav), "-b:a", bitrate, str(mp3)], check=True)
    return mp3


def main(argv: list[str] | None = None) -> int:
    """The render, wrapped in the machine lock: one model-loading process at a time."""
    try:
        with guard.single_instance():
            return run(argv)
    except guard.Busy as e:
        print(f"render: {e}", file=sys.stderr)
        return guard.EXIT_BUSY


def run(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--script", required=True, type=Path)
    ap.add_argument("--engine", default=None, help="apple | kokoro | higgs (default from config.toml)")
    ap.add_argument("--show", default=None, type=Path, help="show folder holding config.toml, prompts/, voices/, out/ (default: $PODGEN_SHOW, else ~/podcasts/default)")
    ap.add_argument("--config", default=None, type=Path, help="config file (default <show>/config.toml)")
    ap.add_argument("--out", default=None, type=Path, help="output directory (default <show>/out)")
    ap.add_argument("--copy-to", default=None, type=Path, help="also copy the mp3 (or wav) and its script here")
    ap.add_argument("--keep-turns", action="store_true", help="keep per-turn wavs")
    ap.add_argument("--name", default=None, help="override the output stem (default: date-script-engine-voices-HHMM)")
    ap.add_argument("--allow-slop", action="store_true", help="render even if the slop gate fails (state the reason in the log)")
    ap.add_argument(
        "--mode",
        default=None,
        help=f"one of {', '.join(modes.names())}; overrides the script's frontmatter (default: the frontmatter, then config.toml [mode].default)",
    )
    ap.add_argument(
        "--allow-rubric-fail",
        action="store_true",
        help="render even if the rubric gate fails (the bypass is recorded in the log)",
    )
    ap.add_argument(
        "--allow-ungrounded",
        action="store_true",
        help="render even if the grounding gate fails (the bypass is recorded in the log)",
    )
    ap.add_argument(
        "--first-render-ok",
        action="store_true",
        help="allow a non-fixture script on an engine that has no successful render recorded yet",
    )
    ap.add_argument(
        "--allow-structure-fail",
        action="store_true",
        help="render even if the structure gate fails (the bypass is recorded in the log)",
    )
    args = ap.parse_args(argv)

    try:
        cfg = load_config(args.config or paths.show_config(args.show))
    except (KeyError, OSError) as e:
        print(f"bad config: {e}", file=sys.stderr)
        return 1

    # One engine per host: --engine wins, else the host's own `engine`, else the default. Each
    # distinct engine is loaded once, and every one of them clears the registry's memory gate
    # before any model is imported.
    engine_name = args.engine or cfg["render"]["engine_default"]
    host_engine_names = {
        s: (args.engine or h.get("engine") or engine_name) for s, h in cfg["hosts"].items() if s in ("s1", "s2")
    }
    loaded: dict[str, object] = {}
    try:
        for name in dict.fromkeys(host_engine_names.values()):
            loaded[name] = engines.load(name)
    except engines.EngineUnavailable as e:
        print(f"engine unavailable: {e}", file=sys.stderr)
        return 2
    engine_slug = "+".join(dict.fromkeys(host_engine_names.values()))

    try:
        # Read once: the parsed turns drive the render, the raw text drives the rubric gate, and
        # the raw bytes are saved beside the audio, so the copy is exactly what was rendered.
        script_bytes = args.script.read_bytes()
        text = script_bytes.decode("utf-8")
        meta, _, _ = strip_frontmatter(text)
        turns = parse_script(text)
        questions = outline(text)
    except (ScriptError, OSError) as e:
        print(f"bad script: {e}", file=sys.stderr)
        return 1

    # The mode comes from the script itself, never from the pipeline's memory, so a saved
    # script gates the same way whoever renders it. --mode overrides the frontmatter.
    mode_name = args.mode or meta.get("mode") or cfg["mode"]["default"]
    try:
        # spec(), not load(): the render needs the rubric weights, not the brief.
        mode_spec = modes.spec(mode_name)
    except modes.ModeUnavailable as e:
        print(f"mode unavailable: {e}", file=sys.stderr)
        return 10

    # The structure gate: host balance, turn length, the outline, banned artifacts. Config
    # drives every threshold, and the host count comes from the script's own format, so a
    # solo format is not read as a missing second host.
    formats = cfg.get("formats", {})
    fmt_name = meta.get("format") or cfg.get("episode", {}).get("format")
    hosts_n = int(formats.get(fmt_name, {}).get("hosts", 2)) if formats else 2
    structural = structure_check(turns, questions, dict(cfg.get("gate", {}), hosts=hosts_n))
    for line in structural:
        print(f"structure gate: {line}", file=sys.stderr)
    if structural and not args.allow_structure_fail:
        print("structure gate failed; fix the script or pass --allow-structure-fail with a reason", file=sys.stderr)
        return 12

    ok, report = slop_gate(args.script, word_count(turns))
    print(report, file=sys.stderr)
    if not ok and not args.allow_slop:
        print("slop gate failed; fix the script or pass --allow-slop with a reason", file=sys.stderr)
        return 3

    rubric_findings = script_rubric.check(text, mode_spec.rubric_weights)
    print(script_rubric.format_report(rubric_findings), file=sys.stderr)
    if rubric_findings and not args.allow_rubric_fail:
        print("rubric gate failed; fix the script or pass --allow-rubric-fail with a reason", file=sys.stderr)
        return 6

    # The grounding gate. The mode's strategy decides whether the script's claims are checked
    # at all; the gate never tests a mode name. The sources come from the script's own
    # frontmatter, so a saved script is checked against the same files it was written from.
    grounding_result = f"skipped (mode {mode_name})"
    grounding_checker = modes.grounding.checker(mode_spec.grounding)
    sources = resolve_source_paths(meta, Path(cfg["_root"]))
    missing = [s for s in sources if not s.exists()]
    if grounding_checker is None:
        # A source this mode does not read is worth saying out loud, and nothing more.
        for source in missing:
            print(f"sources: {source} not found (not needed for mode {mode_name})", file=sys.stderr)
    else:
        refusal = None
        # Only a grounded mode opens these files, so the confinement lives here rather than
        # at resolution: a mode that never reads a source has no business refusing one.
        _, refused = readable_sources(meta, Path(cfg["_root"]))
        if refused:
            refusal = (
                f"grounding gate: mode {mode_name} will not open {refused[0]}; "
                "a source must sit under the show root or under home"
            )
        elif not sources:
            refusal = (
                f"grounding gate: mode {mode_name} needs sources: in the script's frontmatter "
                "and none are listed"
            )
        elif missing:
            refusal = (
                f"grounding gate: mode {mode_name} needs every listed source; "
                f"{missing[0]} is not there"
            )
        else:
            # Imported here, not at module scope: pipeline.py imports this module, so the
            # dependency only runs one way at import time.
            from podcaster import sources as source_readers
            from podcaster.pipeline import SourceError, build_writer_input

            try:
                # The same cap the write step used. Without it the gate scores the script
                # against material the writer never saw, so a claim that happens to appear in
                # the cut-away part passes: the fail-open direction for a gate.
                findings = grounding_checker(
                    text,
                    build_writer_input(
                        sources,
                        max_words=int(
                            cfg.get("sources", {}).get("max_words", source_readers.DEFAULT_MAX_WORDS)
                        ),
                    ),
                )
            except NotImplementedError as e:
                # A strategy named by a mode but not built: the mode cannot run at all.
                print(f"grounding gate: {e}", file=sys.stderr)
                return 10
            except (SourceError, OSError) as e:
                refusal = f"grounding gate: {e}"
            else:
                print(grounding_check.format_report(findings), file=sys.stderr)
                if findings:
                    refusal = "grounding gate failed; fix the script or pass --allow-ungrounded with a reason"
        if refusal is not None:
            print(refusal, file=sys.stderr)
            if not args.allow_ungrounded:
                return 7
            grounding_result = "bypassed with --allow-ungrounded"
        else:
            grounding_result = "passed"

    # Every engine this render loads is resident at once, so the preflight asks for all of them.
    fits, memory_report = preflight(
        [engines.ENGINES[n] for n in dict.fromkeys(host_engine_names.values())], word_count(turns), cfg
    )
    print(memory_report, file=sys.stderr)
    if not fits:
        return 8

    # Fixture-first is per engine, so a two-engine render needs a marker for each: a proven
    # kokoro must not unlock a first full-length higgs render.
    marker_dir = FIRST_RENDER_DIR
    render_engines = list(dict.fromkeys(host_engine_names.values()))
    proven = {n: first_render_marker(n, marker_dir).exists() for n in render_engines}
    for name in render_engines:
        gate_ok, gate_report = fixture_gate(args.script, name, args.first_render_ok, marker_dir)
        print(gate_report, file=sys.stderr)
        if not gate_ok:
            return 9
    if is_fixture(args.script):
        gate_result = "fixture"
    elif all(proven.values()):
        gate_result = "marker"
    else:
        gate_result = "bypassed with --first-render-ok"

    rcfg = cfg["render"]
    hosts = cfg["hosts"]
    # Output name: <dated script stem>-<engine>-<s1 voice>-<s2 voice>-<HHMM>, so re-renders never
    # overwrite. A voice is an id or a clip path; either way the stem is the name.
    voices = [loaded[host_engine_names[s]].voice_for(hosts[s]) for s in ("s1", "s2")]
    voice_slug = "-".join(Path(v).stem.lower().replace(" ", "").replace("(", "").replace(")", "") for v in voices)
    script_stem = args.script.stem if DATED.match(args.script.stem) else f"{time.strftime('%Y-%m-%d')}-{args.script.stem}"
    stem = args.name or f"{script_stem}-{engine_slug}-{voice_slug}-{time.strftime('%H%M')}"
    out_dir = args.out or (Path(cfg["_root"]) / "out")
    turn_dir = out_dir / f"{stem}-turns"
    turn_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.perf_counter()
    turn_wavs: list[Path] = []
    for i, turn in enumerate(turns, start=1):
        speaker_cfg = hosts[turn.speaker.lower()]
        this_engine_name = host_engine_names[turn.speaker.lower()]
        engine = loaded[this_engine_name]
        voice = engine.voice_for(speaker_cfg)
        wav = turn_dir / f"{i:04d}-{turn.speaker}.wav"
        engine.render_turn(
            turn.text,
            voice,
            wav,
            rcfg["sample_rate"],
            speed=speaker_cfg.get("speed", 1.0),
            rate_wpm=rcfg.get("apple_rate_wpm", 175),
            model_id=rcfg.get(f"{this_engine_name}_model"),
            speaker=speaker_cfg,
        )
        turn_wavs.append(wav)
        print(f"  turn {i}/{len(turns)} {turn.speaker} {voice}", file=sys.stderr)
    render_s = time.perf_counter() - t0

    final_wav = out_dir / f"{stem}.wav"
    gaps_cfg = rcfg.get("gaps") or {"default_ms": rcfg.get("gap_ms", 350)}
    plan = gap_plan(turns, section_starts(text), gaps_cfg)
    audio_s = stitch(turn_wavs, final_wav, plan)
    mp3 = to_mp3(final_wav, final_wav.with_suffix(".mp3"), rcfg["mp3_bitrate"])
    if not args.keep_turns:
        shutil.rmtree(turn_dir, ignore_errors=True)

    script_copy = out_dir / f"{stem}.md"
    script_copy.write_bytes(script_bytes)

    deliverable = mp3 or final_wav
    if args.copy_to:
        args.copy_to.mkdir(parents=True, exist_ok=True)
        shutil.copy2(deliverable, args.copy_to / deliverable.name)
        shutil.copy2(script_copy, args.copy_to / script_copy.name)

    # These engines have now finished a render on this machine, so full-length scripts are allowed on them.
    for name in render_engines:
        marker = first_render_marker(name, marker_dir)
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(f"{time.strftime('%Y-%m-%d')} {args.script}\n")

    m = metrics(turns)
    print(
        f"metrics: distinct2 {m['distinct_2']:.2f}, mattr {m['mattr_50']:.2f}, "
        f"turn words mean {m['turn_words_mean']:.0f} max {m['turn_words_max']}, "
        f"questions {m['question_share']:.0%}, S1 share {m['turn_share'].get('S1', 0):.0%}",
        file=sys.stderr,
    )
    log = {
        "script": str(args.script),
        "episode": meta.get("episode"),
        "format": meta.get("format"),
        "engine": engine_slug,
        "engines": host_engine_names,
        "mode": mode_name,
        "voices": {"s1": voices[0], "s2": voices[1]},
        "structure_gate": "passed" if not structural else "bypassed with --allow-structure-fail",
        "slop_gate": "passed" if ok else "bypassed with --allow-slop",
        "rubric_gate": "passed" if not rubric_findings else "bypassed with --allow-rubric-fail",
        "grounding_gate": grounding_result,
        "sources": [str(s) for s in sources],
        "first_render_gate": gate_result,
        "turns": len(turns),
        "words": word_count(turns),
        "metrics": m,
        "gaps_ms": plan,
        "audio_seconds": round(audio_s, 1),
        "render_seconds": round(render_s, 1),
        "rtf": round(render_s / audio_s, 3) if audio_s else None,
        "wav": str(final_wav),
        "mp3": str(mp3) if mp3 else None,
        "script_copy": str(script_copy),
        "copied_to": str(args.copy_to / deliverable.name) if args.copy_to else None,
    }
    (out_dir / f"{stem}.json").write_text(json.dumps(log, indent=2) + "\n")
    print(json.dumps(log, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
