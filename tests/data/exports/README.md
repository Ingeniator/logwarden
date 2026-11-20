Real exports of `examples/bad.jsonl` (v0.8.0 claims 2 and 3), made on 2026-11-20 with Fluent Bit 3.1.9,
Elasticsearch 8.15.3 and Kibana 8.15.3 (docker):

- Fluent Bit tailed `bad.jsonl` twice: without a parser (`fb-raw`: the line in `log`) and with the `json`
  parser (`fb-merged`: fields at the top level; the plain-text line keeps `log`). Filters added
  `kubernetes.{pod_name,namespace_name}` and `stream`. Its `file` output is `fluent-bit-*.jsonl`.
- `es-raw.search.json`: `_search` sorted by `@timestamp`. `es-merged.scroll.ndjson`: two scroll pages
  (size 3), one per line. `es-raw.elasticdump.ndjson`: `elasticdump --type=data`.
- `kibana-*-all-fields.csv`: Kibana reporting `csv_searchsource` with no columns (all fields).
  `kibana-merged-picked-columns.csv`: chosen columns without `log`, so the plain-text line can't be
  rebuilt from it (only its pipeline fields are there).

Kibana 8 writes `-` for missing values, adds `.keyword` copies of text fields, formats dates, and exports
a `_source` column as `-`.
