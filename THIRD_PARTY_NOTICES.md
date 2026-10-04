# Third-Party Notices

YOmux is licensed by Keiven Chang under PolyForm Noncommercial 1.0.0. This file records third-party software and generated bundles that keep their own upstream notices.

Do not replace third-party notices with the YOmux project license. They are not owned by Keiven Chang.

## Runtime Python Dependency

- PyYAML - YAML parser/emitter dependency declared in `pyproject.toml`; package metadata reports MIT license.

## Runtime Go Dependency

- `github.com/mattn/go-sqlite3` 1.14.52 - SQLite driver used by the Go backend and declared in `go.mod`; its upstream `LICENSE` is MIT.
- `golang.org/x/sys` 0.48.0 - system-call interfaces declared in `go.mod`; its upstream `LICENSE` is BSD 3-Clause.

## Vendored Browser Assets

- Dockview Core 6.6.1 - `static/vendor/dockview-core.noStyle.js` and `static/vendor/dockview.css`; upstream package metadata and bundle headers report MIT license.
- Mermaid 11.15.0 - `static/vendor/mermaid.min.js`; upstream package metadata and bundle headers report MIT license and include bundled notices for transitive components including DOMPurify, js-yaml, lodash, Underscore-derived code, and other browser-side libraries.

## Generated Browser Bundles

- CodeMirror 6 packages and `esbuild` are used to rebuild `static/codemirror.js` from `tools/codemirror-bundle/codemirror-entry.js`; `tools/codemirror-bundle/package-lock.json` records MIT license metadata for those packages.
- ProseMirror packages and `esbuild` are used to rebuild `static/prosemirror.js` from `tools/prosemirror-bundle/prosemirror-entry.js`; `tools/prosemirror-bundle/package-lock.json` records MIT license metadata for those packages and their Markdown parser dependencies.

## Development/Test Dependencies

- `pytest-xdist` is declared in the `dev` extra in `pyproject.toml` for local test parallelism.
- Optional managed-agent SDK packages are declared in the `yoagent` extra in `pyproject.toml`; their upstream licenses remain separate from the YOmux project license.
- Bazel `rules_go` 0.64.0, Gazelle 0.54.0, `rules_shell` 0.6.1, `platforms` 1.1.0, and Buildifier are repository build dependencies. These tools carry Apache License 2.0 upstream licenses; preserve their license files and notices when redistributing them.
