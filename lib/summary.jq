# Weaver live_check.json -> compact summary used by `logwarden check`.
# Collects findings from both record-level and attribute-level results.
# Weaver 0.28 emits {id, level, message}; older builds used advice_type/advice_level.
[ .. | objects | select(has("all_advice")) | .all_advice[]
  | {rule: (.id // .advice_type), level: (.level // .advice_level), message} ] as $all
| ($all | map(select(.level == "violation"))) as $v
| {
    samples: ((.samples // []) | length),
    violations: ($v | length),
    by_rule: ($v | group_by(.rule)
                 | map({rule: .[0].rule, count: length, example: .[0].message})
                 | sort_by(-.count)),
    other_advice: ($all | map(select(.level != "violation"))
                        | group_by(.rule)
                        | map({rule: .[0].rule, level: .[0].level, count: length}))
  }
