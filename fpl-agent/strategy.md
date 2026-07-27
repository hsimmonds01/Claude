# Strategy

Plain-English settings the agent reads. Edit freely; it takes effect on the
next run. The `key: value` lines are parsed, everything else is context the
email writer uses.

    risk: 0.5
    max_hit: 4
    horizon: 5

## What risk means

FPL is scored on rank, not points. That makes owning a very popular player
different from owning an equally good unpopular one: if 75% of managers have
Haaland and he hauls, owning him keeps you level while missing him drops you
sharply. The points are the same either way — the *rank consequence* isn't.

`risk` is how much that matters, from 0 to 1:

- **0.0** — pure points maximisation. Ignores ownership entirely. Produces
  the highest projected score and the widest rank swings.
- **0.5** — template-led with room for differentials. Popular high-scorers
  get pulled in, but a differential can still win a place by projecting
  clearly better. **Current setting.**
- **1.0** — full template. Essentially mirrors what everyone else owns.

Raising `risk` will *lower* the projected points total. That is the trade,
not a bug: you are buying protection against rank collapse with a small
amount of expected score.

## Preferences

- Mainly template, with a few genuine differentials — not contrarian for its
  own sake. A differential has to out-project the template alternative to
  earn a place.
- Never take more than a −4 hit in a single gameweek (`max_hit`).
- Flag when the model and the news disagree rather than silently picking one.
- Club moves, new managers and heavy-rotation clubs are context to weigh, not
  automatic penalties — see minutes.py for what was tested and rejected.

## Notes on specific clubs

- **Man City** — historically the heaviest rotators under Guardiola (2.89 and
  3.06 XI changes per week in 23/24 and 24/25, easing to 2.40 in 25/26). With
  a new manager the historical figure is a weak guide at best. Treat City
  minutes as uncertain until a pattern establishes this season.
- **Chelsea** — the heaviest rotation in the league last season (3.33 changes
  per week) and rising across all three seasons.
