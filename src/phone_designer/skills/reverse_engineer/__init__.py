"""Wave4-A reverse_engineer — feature catalog + plan reconstruction skills.

Two atomic, body-preserving skills:

  - extract_feature_catalog: runs every detector / classifier in sequence
    and aggregates a single feature_catalog dict.
  - plan_from_feature_catalog: turns that catalog into an ordered Plan YAML
    of build skills (hole / pocket / boss / rib / lug / pattern).
"""
