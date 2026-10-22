## Docs Alex reads (2026-10-15)

Alex, 2026-10-15: "retire the doc template completely". Every plan, report, explainer and review Alex reads is an Agent Native Plan doc: write Plan MDX, put each question on its exact sentence with a Recommended answer in a questions JSON, and run `plan-publish <doc.mdx> --questions <q.json> --owner <pane>`, which prints the doc's `/plans/<id>` URL. Republish with `--replace <old id>`. Sources live in `~/.agent-rails/factory/plan-docs/<slug>/`. The htw doc template, doc-kit HTML and pretty-docs are retired: a prototype, film or image goes inside the Plan doc with `--assets DIR` or `--attach`, a page a tool rewrites keeps its URL with `--into <id>`, and 8799 and 8844 only redirect old links until 2026-11-14.

