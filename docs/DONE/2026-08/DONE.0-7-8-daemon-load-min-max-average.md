# v0.7.8 Daemons load Min, Avg, and Max completion

Completed the Daemons load queue with one line per service and one accessible `Avg` / `Max` / `Min` selector. Fresh fine-resolution views choose Avg; fresh 60s and 300s views choose Max; an explicit selection persists. The shared chart renderer retains Range, Resolution, zoom, chart size, theme, locale, gaps, CPU above 100%, and complete Min/Avg/Max hover context.

The exact materializer stores three sibling folds from original observations for both daemon load and host/process CPU. The browser projects the selected fold and never reconstructs extrema from an average. A shared visible-service classifier prevents hidden average-only legacy rows from disabling valid extrema, one retained-item initializer owns apply/merge, and the client mirrors the server's 1 Hz service-load cadence so 10s buckets contain multiple real samples.

Red-first evidence covered average-only projection, genuine 300s and 10s extrema, host/process persistence, hidden legacy rows, cadence drift, duplicate initialization, and real mouse-driven selector/hover/persistence behavior. Focused results included 79/79 panel tests, 14/14 cadence/collector tests, all Node layout shards, distinct live 10s/60s/300s folds, and Keiven's acceptance of the live distinct plots.

Composed landing evidence followed Keiven's reduced evidence policy. The 25%-CPU integrated gate passed the Daemons covering static, compile, syntax, whitespace, Node, browser, non-browser, and serial lanes. Two unrelated YO!agent E2E nodes exposed a shared detector bug, were fixed at `0400dbe8a`, and passed alone; the full gate was not repeated. Certification-only certified 7/7 units on qualified clean SHA `3be481152` in 103.17 seconds.
