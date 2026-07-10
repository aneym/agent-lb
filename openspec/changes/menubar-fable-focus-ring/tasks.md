# Tasks

- [x] Observe frontmost-app activation in AppState and expose
      `isClaudeFrontmost` (bundle id prefix `com.anthropic.`), recomputing the
      status icon on change.
- [x] Compute pool Fable remaining % (mean over routable Anthropic accounts
      with scoped data) and feed it to the status icon renderer.
- [x] Render the Fable cell: widened 38 pt icon with a third ring + `F`
      glyph, cache-keyed with the existing 4 % buckets.
- [x] Unit-test pool math, bundle matching, and icon geometry; run the Swift
      suite.
- [x] Live-verify: focus Claude → cell appears with an arc matching the
      API-derived pool %; focus another app → cell disappears.
