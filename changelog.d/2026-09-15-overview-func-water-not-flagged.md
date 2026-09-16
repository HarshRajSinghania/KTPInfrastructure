### `scripts`: overview renderer draws func_water as terrain (2026-09-15)

`render_overview_bmp.py` decided a face was water from its texture name alone
(`texture.startswith("!")`), so a `func_water` brush textured with ordinary names rendered as
terrain and was height-shaded like the ground. On `dod_saints2_b4e` the canal surface sits at
z -416, which shaded it near-black: the map's central waterway read as a dark trench running
under the bridges rather than as water.

`func_water` was already in `DRAWN_BRUSH_CLASSES`, so the faces were being drawn; what was
missing is that `select_faces` discarded the `model_index` `upward_faces` yields, leaving no
way to tell which model a face belonged to. It now keeps it and treats any face of a
`func_water` model as water.

Re-rendering the eleven S10 maps against the previous renderer: nine are byte-identical, and
only the two maps whose water brushes carry non-`!` textures change --- `dod_saints2_b4e`
(19,375 px) and `dod_pandemic_aim` (22,464 px). Maps whose water already used `!`-prefixed
textures are unaffected, because the texture test still applies.
