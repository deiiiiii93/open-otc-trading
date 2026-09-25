If the conversation context names a selected pricing parameter profile, include
its `pricing_parameter_profile_id` in any `run_batch_pricing` or portfolio/risk
`create_report` call. If none is selected, use the profile the instruction
names, resolving the name with `list_pricing_parameter_profiles`; if the
instruction names none, run without one and say so in your answer. Do not
invent pricing parameter profile ids from names.
