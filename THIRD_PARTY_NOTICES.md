# Third-Party Notices

YOLOmux is licensed by Keiven Chang under PolyForm Noncommercial 1.0.0. This file records third-party software and generated bundles that keep their own upstream notices.

Do not replace third-party notices with the YOLOmux project license. They are not owned by Keiven Chang.

## Runtime Python Dependency

- PyYAML - YAML parser/emitter dependency declared in `requirements.txt`; package metadata reports MIT license.

## Vendored Browser Assets

- Dockview Core 6.6.1 - `static/vendor/dockview-core.noStyle.js` and `static/vendor/dockview.css`; upstream package metadata and bundle headers report MIT license.
- Mermaid 11.15.0 - `static/vendor/mermaid.min.js`; upstream package metadata and bundle headers report MIT license and include bundled notices for transitive components including DOMPurify, js-yaml, lodash, Underscore-derived code, and other browser-side libraries.

## Generated Browser Bundles

- CodeMirror 6 packages and `esbuild` are used to rebuild `static/codemirror.js` from `prototypes/codemirror-entry.js`; `prototypes/codemirror-bundle/package-lock.json` records MIT license metadata for those packages.

## Development/Test Dependencies

- `pytest-xdist` is declared in `requirements-dev.txt` for local test parallelism.
- Optional managed-agent SDK packages are declared in `requirements-yoagent-managed.txt`; their upstream licenses remain separate from the YOLOmux project license.
