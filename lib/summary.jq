# Weaver live_check.json -> logwarden result.
# Args: $lines  = [[input line number per sample]]   (--slurpfile)
#       $rules  = [rules.json]                       (--slurpfile)
#       $meta   = {"total_lines": N, "version": "x"} (--argjson)
# Weaver 0.28 emits {id, level, message}; older builds used advice_type/advice_level.

def norm: {rule: (.id // .advice_type), level: (.level // .advice_level),
           field: (.context.attribute_key // null), message};

($lines[0]) as $ln
| ($rules[0]) as $r
| [ (.samples // []) | to_entries[]
    | .key as $i | .value.log as $log
    | [ $log | .. | objects | select(has("all_advice")) | .all_advice[] | norm ][]
    | . + {line: $ln[$i], event: $log.body} ] as $all
# A field already flagged by a logwarden naming rule is not also reported as unknown.
| ([ $all[] | select(.rule == "adr_banned_synonym" or .rule == "adr_semconv_prefix")
     | "\(.line)|\(.field)" ] | map({(.): true}) | add // {}) as $named
| ($all | map(select(.level == "violation")
              | select((.rule == "missing_attribute" and $named["\(.line)|\(.field)"]) | not))) as $v
| {
    tool: "logwarden",
    version: $meta.version,
    ok: (($v | length) == 0),
    records: ((.samples // []) | length),
    skipped_lines: ($meta.total_lines - ((.samples // []) | length)),
    violations: ($v | length),
    by_rule: ($v | group_by(.rule)
                 | map({rule: .[0].rule, count: length,
                        title: ($r[.[0].rule].title // null),
                        hint: ($r[.[0].rule].hint // null),
                        doc: ($r[.[0].rule].doc // null),
                        example: .[0].message})
                 | sort_by(-.count)),
    findings: ($v | sort_by(.line) | .[0:500]
                 | map({line, event, rule, field, message})),
    findings_truncated: (($v | length) > 500),
    advice: ($all | map(select(.level != "violation"))
                  | group_by(.rule)
                  | map({rule: .[0].rule, level: .[0].level, count: length}))
  }
