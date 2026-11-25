# Rules for writing an episode

These rules apply to both hosts and to any writer, human, in-session or local model. The material is every file under `in/`; nothing outside it is in the sources. Each host also has a persona file; read both.

The failure these rules exist to prevent: a script that is accurate, fully cited, and dead. Every turn proved something, the hosts believed nothing, nothing was at stake until minute thirty, and there was not one laugh in forty minutes. Careful was the wrong target. Two published findings say why: scripts written by a model score higher than real podcasts on lexical polish and lower on information density, so polish is a tell of the machine rather than a mark of quality (PodEval, 2025); and a pipeline where profiled hosts actually interact before the script is written beats a single model writing the dialogue straight (PodAgent, ACL 2025). Write the interaction, not the summary.

## The spine

Before any turn, write one sentence at the top of the draft, as a comment line starting with `# spine:`, naming the single question the episode rides. Every scene serves it. If a scene does not serve it, cut the scene. The spine comes from the material: the thing the listener asked that cannot be answered from inside the material as written, sharpened until it has stakes.

Under the spine, write three to five comment lines starting `# q:`, one sub-question each, in the order the episode answers them. They are the outline; every scene answers one of them, and the last one is the spine paid off. Write them before the first turn and do not change them after. With `require_outline = true` in `config.toml`, the render refuses a script without them. Then place each `# q:` line a second time in the body, as a comment directly above the turn that opens that scene; the render puts a longer pause there.

When the brief carries a Structure list, the episode follows it in that order, one or more questions per item. When it carries a Focus, that instruction outranks the personas' default emphasis for this episode only. When it names a Format, put `format: <name>` in the frontmatter; a one-host format is all `[S1]` lines, spoken to the listener, and the second persona does not appear.

Open on a scene, in the first line, in sequence: this happened, then this, then this. Sequence raises the question of what comes next, and that question is what keeps someone listening. Put the stakes in the first minute. Pay the spine off in the last two turns. The listener's own lines, where the sources carry them, are the best scenes you have; quote them verbatim and build around them.

## Two hosts

- Each host has a stance on the spine, stated early and held. They add to each other and change each other's minds on the evidence, and they never stage a debate, never take opposing roles for effect, never referee. Two readings can both be right; say what each rests on and let the listener hold both.
- Give them reactions. A host can be surprised, amused, unconvinced, moved. A host can laugh. Dark humor is allowed; a joke that lands on the situation, never on the listener.
- Differentiate them by what they do, not by adjectives. If the listener could not tell who said a line with the tag removed, rewrite it.
- Remove redundancy, preserve distinct viewpoints. No turn restates the previous turn in other words, and no host summarizes what the other just said. When both would say the same thing, one says it and the other adds only what they alone would notice. If a turn adds nothing the listener did not already have, cut it.
- No turn runs past about 120 words. A long point is two turns with the other host in between.

## Devices

Use these on purpose: rhetorical questions, a question from one host to the other, an anecdote from the sources told as a scene, an analogy, a callback to something said twenty minutes earlier, a reversal ("they thought X would happen, then Y happened"), and humor. Two questions are the reflection engine: what was expected to happen, and what actually happened. Run them on every scene that has both halves in the record.

Reaction tokens ("right", "yeah", "hm", "okay") are allowed a few times an episode where a person would actually say them. "Exactly," "absolutely," "you've hit the nail on the head" and "great question" are the canned tells of machine podcasts; never.

## Citations

An episode gets at most eight named sources, counting the listener's own notes as free. A guide or a study earns a name when its line changes what the listener would do or lands as a punch; it never earns a name to prove the reading was done. Say who and what in one clause and move on. Numbers get named when they change a decision; otherwise say "the record" or "the sheet" and keep talking.

## Address

Three modes, and the script moves between them the way two people in a room with a third would: second person to the listener ("you logged one and three tenths on Tuesday"), third person about the listener between the hosts ("they wrote that they don't need the why"), and second person between the hosts ("you're reading that as the budget talking"). Switch at turn boundaries, never mid-sentence, and make the target clear from the words. Never the listener's name in any mode. Never explain the listener's own life back to them. Never put a conclusion in the listener's mouth; "you" does not license telling them what they concluded.

## Voice, written for the ear

- Sentences a person would say. Short beats long; short means fewer clauses, not clipped fragments in a row. Dependent clauses stacked in one sentence are toxic to radio; split them.
- Fragments are allowed where a person would use one. Contractions always.
- Intense and deep where the material is, terse where it is not. Never stuffy, never corny, never a pep talk, never a lecture.
- No emoji, no hook lines, no teaser constructions, no sentence that announces what the next sentence will do, no name stacks (two named sources in one turn at most). American spelling.
- Only people, studies and events present in the material. If something is not in the sources, a host says so instead of filling it in; that line is the last sentence of the episode.

## Texture, measured on Kokoro

An ellipsis is the longest beat the engine gives, about a quarter second; a comma or a spaced hyphen is a short breath; a question mark lifts the line end; markdown is read aloud as the word asterisk. Write for that.

- Vary sentence length inside a turn. A long sentence, then a short one.
- An ellipsis for a beat before the point lands... like that. Where a person would pause, and nowhere else.
- A spaced hyphen for an aside - the kind a host drops mid-thought - closed the same way.
- Turns of roughly 20 to 45 words. Under about eight words the voice goes flat and rushed; bundle a short line into the neighboring turn unless the short line is the point.
- No markdown inside a line: no asterisks, no underscores, no backticks, no brackets other than the speaker tag.
- Numbers, names and acronyms spelled the way they should sound. Ninety-two, not 92. A D R, not ADR. Plain letters, no accents.

## Format

One turn per line. The speaker tag, a space, the line. Frontmatter with `date`, `episode`, `words`, and a `# spine:` comment line after it. Length is the word budget in `config.toml`, `target_minutes` times `words_per_minute`, within ten percent. The shape, with placeholder lines:

```
---
date: 2026-09-17
episode: A
words: 4000
---
# spine: the one question this episode rides, with stakes
[S1] The opening scene, told in sequence. This happened. Then this... and then the thing nobody expected.
[S2] Which has happened before. The new part is what came next - and that's the part that changes the read.
```

Save to `scripts/<date>-<episode>[-v<n>].md`, one file per version, never overwriting a rendered one.

## Before the script is done

Read it back by ear, not by eye. Then run the slop linter on the saved file; the renderer runs it again and refuses a script that fails it. The linter enforces two things you will trip on: no "X, not Y" antithesis beyond a couple per episode, and no characters a keyboard cannot type. A verbatim quote that trips a warning stays as written. Last check, the rubric the field uses to score these scripts: coherence, engagingness, diversity, informativeness, and whether the two speakers are distinguishable. If the fifth fails, the script is not done.
