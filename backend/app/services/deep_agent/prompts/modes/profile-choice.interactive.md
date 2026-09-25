If the conversation context names a selected pricing parameter profile, include
its `pricing_parameter_profile_id` in any proposed `run_batch_pricing`
or portfolio/risk `create_report` call. If no pricing parameter
profile is selected, ask which profile to use before proposing those persisted
pricing/risk/report writes, unless the user explicitly says to run without one.
For `run_batch_pricing`, this profile-choice clarification is mandatory before
delegation: do not ask a persona to propose or call `run_batch_pricing` with a
missing profile choice.
If the user names a pricing parameter profile but the context does not provide
an id, resolve it with `list_pricing_parameter_profiles` before proposing the
write. Do not invent pricing parameter profile ids from names.
