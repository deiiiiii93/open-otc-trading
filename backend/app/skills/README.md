# Agent Skills

Phase 3 keeps the shared runtime skill catalog in this `app/skills` root.

- `workflows/` holds executable workflow skills loaded through
  `SkillsMiddleware`.
- `meta/` holds always-in-context runtime policy fragments composed into agent
  prompts.
- `references/` holds durable non-executable domain references that workflows
  may read explicitly.
- P3.8 removed routing skills; compound routes are covered by router-contract
  tests and orchestrator prompt instructions.
- P3.9 removed the transitional `legacy/` subtree and the compatibility
  aliases `domains`, `procedures`, and `products`.
