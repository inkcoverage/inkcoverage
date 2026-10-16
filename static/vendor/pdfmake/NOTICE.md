# Local PDF report dependencies

pdfmake **0.3.11**, from the official npm registry. The tarball's SHA-512 npm integrity was verified before copying the browser build and VFS. SHA-256 file hashes are recorded in manifest.json. pdfmake is MIT licensed (LICENSE).

The VFS includes Roboto 3.014, copyright The Roboto Project Authors, under the SIL Open Font License (Roboto-OFL.txt). The optional Noto Sans SC regular font is copied unchanged from the user's LabelEstimate quote assets, also under the SIL Open Font License (NotoSansSC-OFL.txt). Its SHA-256 is recorded in the manifest. No font files in LabelEstimate were modified.

Report generation lazy-loads these local files only after Report is clicked. No CDN or external PDF/font service is contacted. The browser keeps generated PDFs locally. scripts/vendor-report-assets.py records the repeatable integrity check, asset extraction and Unicode coverage map construction.

The report's A4 / 40-point side margins / 60-point footer space / Roboto typography / thumbnail and metadata arrangement / light table rules follow the existing LabelEstimate quote definition in its src/App.jsx. The document content and attribution identify InkCoverage.
