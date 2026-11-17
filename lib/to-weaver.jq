# structlog JSON line -> Weaver live-check log sample
def sev: {"debug":5,"info":9,"warning":13,"warn":13,"error":17,"critical":21,"fatal":21}[(. // "info")|ascii_downcase] // 9;
def wtype: if type=="boolean" then "boolean" elif type=="number" then (if (.|floor)==. then "int" else "double" end) else "string" end;
def wval: if type=="object" or type=="array" then tojson elif type=="null" then "" else . end;
[ .[] | select(type=="object") | {log: {
    # event_name stays empty: ADR events are not declared in the registry, so
    # Weaver would flag each one as missing_event. The event goes in body.
    event_name: "",
    body: (.event // ""),
    severity_number: (.level|sev),
    severity_text: ((.level // "")|ascii_upcase),
    trace_id: (.trace_id // null),
    span_id: (.span_id // null),
    attributes: [ to_entries[]
      | select(.key|IN("timestamp","level","event","trace_id","span_id")|not)
      | {name: .key, type: (.value|wtype), value: (.value|wval)} ]
}} ]
