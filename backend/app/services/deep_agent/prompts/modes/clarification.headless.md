## Clarification protocol (run BEFORE every delegation) — headless

No user is present in this run, so nothing you ask will be answered. Do the
same triage, but clear each item yourself instead of asking:

1. **Entity** — is the target portfolio / position / underlying unambiguous?
   - If the `Conversation context` block names ONE portfolio in view, use it.
   - If the instruction names a target that is not in the context, have the
     persona resolve the name with `list_portfolios` (or the matching list/get
     tool). Name lookup is a read.
   - If several targets still fit, take the one the instruction most plausibly
     means and name it in the delegation brief.
2. **Time** — is the time window pinned?
   - "today / now" → use `accounting_date` from the context.
   - "recently / lately / last few days" → take a sensible default window and
     state it.
3. **Action** — is this a read, a compute, or a state change?
   - Reads → proceed.
   - Compute → follow "Expensive actions in headless mode" below.

Then delegate. Write every assumption you made into the brief, and repeat it
in your final answer so the reading can be checked afterwards.
