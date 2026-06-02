"""PMI / GD&T category — Product & Manufacturing Information.

Skills here attach drawing-grade annotations (dimensions, surface texture,
welds, FCFs, datums) to the body as plain Python tags (``body._pd_pmi_*``)
and provide a STEP AP242 export path that bundles the annotations alongside
the geometry. All skills are read-only with respect to the OCCT shape —
they only touch metadata.
"""
