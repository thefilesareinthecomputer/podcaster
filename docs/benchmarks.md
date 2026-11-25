# Benchmarks

RTF is the real-time factor: render seconds divided by audio seconds.
Lower is faster. All runs are warm (model already downloaded) unless noted.

## M2 Ultra, 64GB

| engine | script | words | audio s | render s | RTF |
|---|---|---|---|---|---|
| kokoro | 49 turns | 1600 | 755.0 | 50.0 | 0.066 |
| higgs v2 3B q8, cloned voices | 49 turns | 1600 | 1114.0 | 391.0 | 0.35 |
| apple | fixture, 6 turns | 185 | 59.2 | 6.3 | 0.106 |
| kokoro | fixture, 6 turns | 185 | 84.5 | 11.3 | 0.134 |
| higgs v2 3B q8, cloned voices | fixture, 6 turns | 185 | 145.2 | 51.4 | 0.354 |
| kokoro, digest mode | 47 turns | 1546 | 762.1 | 21.1 | 0.028 |
| higgs v2 3B q8, digest mode | 47 turns | 1546 | 1070.8 | 333.1 | 0.311 |

Higgs speaks more slowly from the shipped reference clips,
so the same script runs longer than on Kokoro.

Uncapped, Higgs's MLX buffer cache reached 50GB.
`guard.cap_mlx_memory` holds it at 4GB.

On the two 47-turn renders, `ffprobe`'s duration matched the logged
`audio_seconds` to the millisecond across 46 turn boundaries.

## M1, 16GB

| engine | script | words | audio s | render s | RTF |
|---|---|---|---|---|---|
| apple | fixture, 6 turns | 123 | 34.1 | 7.9 | 0.23 |
| kokoro, cold (model download) | fixture, 6 turns | 123 | 41.4 | 58.5 | 1.41 |
| kokoro | fixture, 6 turns | 123 | 41.4 | 9.2 | 0.22 |
| apple, 175 wpm | 88 turns | 3183 | 893.5 | 98.8 | 0.11 |
| kokoro | 88 turns | 3183 | 1028.8 | 123.5 | 0.12 |
| kokoro | 127 turns | 4965 | 1650.8 | 189.1 | 0.12 |
| kokoro | 111 turns | 5371 | 2330.6 | 262.7 | 0.11 |
| kokoro | 106 turns | 3807 | 1763.0 | 203.1 | 0.12 |
| kokoro, digest mode | 47 turns | 1546 | 761.4 | 93.8 | 0.12 |

On 16GB, Kokoro renders a 30-minute episode in under four minutes,
at an RTF of 0.11 to 0.12 whatever the script length.

Apple at 175 words per minute runs shorter than Kokoro on the same script.
Lower `apple_rate_wpm` toward 150 to match Kokoro's pace.

Two concurrent Kokoro renders push a 16GB machine into memory pressure,
which is why the machine lock allows one run at a time.

## Local writer

`mlx_lm` with a 4-bit 27B model wrote 14 turns (706 words) in 90.7 seconds
on the M2 Ultra, in a child process.
The plumbing works, but the scripts are not yet good enough to use,
so `manual` stays the default writer.
