# Prepared Static Assets

`dockview-core.noStyle.js` and `dockview.css` are copied from `dockview-core` 6.6.1.

Keep this directory limited to runtime dependencies that the browser serves directly. Dependency payloads are downloaded during explicit setup/build and ignored by Git. `assets/runtime/browser-assets.json` records archive hashes, selected archive members, output hashes, and the existing Dockview JavaScript newline/trailing-space normalization. Do not edit vendored files by hand; update them from the upstream package and record the source here.

- Source: https://github.com/mathuo/dockview
- Package: https://www.npmjs.com/package/dockview-core
- License: MIT, as declared in the package metadata and bundle header.

`mermaid.min.js` is copied from `mermaid` 11.15.0.

- Source: https://github.com/mermaid-js/mermaid
- Package: https://www.npmjs.com/package/mermaid
- License: MIT, as declared in the package metadata and bundle header.

`xterm.css` and `xterm.js` are copied from `@xterm/xterm` 6.0.0. `xterm-addon-unicode11.js` is copied from `@xterm/addon-unicode11` 0.9.0.

- Sources: https://github.com/xtermjs/xterm.js and https://github.com/xtermjs/xterm.js/tree/master/addons/addon-unicode11
- Packages: https://www.npmjs.com/package/@xterm/xterm and https://www.npmjs.com/package/@xterm/addon-unicode11
- License: MIT, as declared in the package metadata and bundle headers.

`highlight-github-dark.min.css` and `highlight.min.js` are copied from Highlight.js 11.9.0. `marked.min.js` is copied from Marked 12.0.2.

- Sources: https://github.com/highlightjs/highlight.js and https://github.com/markedjs/marked
- Packages: https://www.npmjs.com/package/highlight.js and https://www.npmjs.com/package/marked
- Licenses: BSD-3-Clause for Highlight.js and MIT for Marked, as declared in the package metadata and bundle headers.

`katex/` is prepared from the pinned archive and contains KaTeX 0.16.22's browser renderer, auto-render extension, CSS, WOFF2 fonts, and license.

- Source: https://github.com/KaTeX/KaTeX
- Package: https://www.npmjs.com/package/katex
- License: MIT, as declared by the package.
- Source tarball SHA-256: `e9e0d167db3175481cbadaff38e8d90b130f6a3ddb451a47e43c577fd511f365`.

`pdfjs/` is downloaded by native setup/build and ignored by Git. `assets/runtime/pdfjs-assets.json` pins PDF.js 6.5.438, its archive checksum, and the checksums of the 401 runtime files used by PDF and TeX previews. The unused renderer worker and PDF scripting engine are excluded. Fonts, character maps, image decoders, color profiles, translations, viewer controls, and their licenses remain available for PDFs that need them.

- Source: https://github.com/mozilla/pdf.js
- Distribution: https://github.com/mozilla/pdf.js/releases/download/v6.5.438/pdfjs-6.5.438-dist.zip
- License: Apache-2.0, included in `pdfjs/LICENSE`.
- Distribution SHA-256: `fcbe9ba21d9911843e3502adc178a7f549a524a4055f887bedc942e5490c6ba1`.

Setup verifies an existing local tree before making any network request. A verified archive in the user cache permits offline installation or repair. Failed downloads are retried; reviewed alternate URLs and release metadata allow renamed asset paths to be discovered. Every candidate must match the pinned archive checksum and every extracted file checksum. Setup never silently upgrades to a different release, replaces a working tree with partial content, or downloads from browser requests or server startup. If no verified source is available, setup fails with the failed URLs and keeps the previous assets; rerun setup when connectivity returns or update the manifest with reviewed URLs and checksums.

Fonts retain their exact published bytes through immutable revision URLs because their original WOFF2 conversion recipe was not recorded. Their upstream family, version, and license remain documented in `THIRD_PARTY_NOTICES.md`. Unicode Emoji 16.0 and CLDR 47.0.0 inputs generate the emoji catalog during setup; generated locale copies and editor bundles are also excluded from Git. Editor source entries and npm manifests/locks remain under `tools/`. The native installer builds editor bundles in a private directory, verifies their output hashes, and caches them for later offline installation.
