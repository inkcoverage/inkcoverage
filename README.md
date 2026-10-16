# InkCoverage.app — MK2

Free, open-source PDF ink coverage analyzer for flexo, offset and digital prepress. Measures average coverage for each CMYK and spot-color channel over a page or selected area, with Copy, CSV and PDF reports. A separate ink-limit check reports the highest rendered total and area above a chosen limit, with heatmap/overlay and selectable inks. Channel previews support isolation and black display. Results are estimates; the tool does not certify print readiness or calculate ink mass/cost.

Version 2.0 is live at https://inkcoverage.app in seven languages as of 16 October 2026. The separate https://beta.inkcoverage.app remains available for testing. Both use certificate-validated origin HTTPS.

## Local preview

Use Python 3.12 and Ghostscript 10.08.0. Create a virtual environment and install requirements.txt. No production connection is required for development.

Set `GS_EXECUTABLE` to the Ghostscript executable and `TEMP_DIR` to a writable temporary root. Run `uvicorn app:app --host 127.0.0.1 --port 8001 --workers 1 --no-proxy-headers --no-access-log --limit-concurrency 32`. Open http://127.0.0.1:8001. The released Linux ARM64 runtime uses Python 3.12.15 and Ghostscript 10.08.0.

Compose, when available, binds loopback port 8000 with one non-root worker, static mounted read-only and resource limits. `.env.example` lists application environment defaults; Python does not load it automatically. Compose currently interpolates only TRUSTED_PROXY_IPS from `.env`; its other settings are explicit in docker-compose.yml.

## Behavior

- English, Spanish, Portuguese, Simplified Chinese, German, French and Ukrainian routes render welcome content before JavaScript. All seven languages include About, FAQ, Contact and Privacy pages, Help and exports. Real language links support direct URLs/new tabs; ordinary clicks preserve the open PDF, selection and results.
- Original dark desktop three-pane design and stacked mobile workflow. Desktop panes fit between the header and footer and scroll internally when needed. Fit/150%/200%/500%, scrolling, hand mode and Spacebar panning only change the view. Test-file captions remain below the preview. Known spot swatches use the document's colour definition; unavailable colours retain the neutral marker.
- Results belong to their document/page/selection. Selection changes clear readings; unmeasured pages show no result. Matching completed results survive source expiry for export.
- Analyze opens a Current page / All pages / Page range dialog, with explicit batch outcomes, stop, selective retry and resume. Page size is shown before cropping; mm/in applies to both page and selection sizes.
- Exports include measured pages, dimensions/position in mm or inches, available declared profiles, warnings and incomplete-batch notes. Filenames identify actual measured groups. CSV has comma separators, decimal-point numbers, UTF-8 BOM and quoted text; select those settings in regional spreadsheet import dialogs.
- Defaults: uploads 50 MiB/100 pages; by-IP rolling hourly quotas 10 uploads, 120 previews, 30 analyses. Busy/failed admitted work may count. Quotas are shared by IP in one worker's memory.
- Preview/analysis access expires 600 seconds after upload. Close clears only the browser view. Cleanup runs on startup/every 60 seconds, protects active workers and retries failures; physical deletion may follow expiry. Rendering, time, disk and concurrency bounds also apply; workload.py defines them.
- RGB/unknown warnings and declared profiles are informational, not output-profile selection. Analysis stays at 72 dpi and preview at 150 dpi. No new precision/resolution controls.
- Local fonts/assets; no analytics/ad scripts. Only language/units are browser preferences; PDFs/results are not saved in localStorage. See the translated privacy pages for provider/contact/operational limitations.

## Source and verification

app.py serves API/public routes. workload.py, lifetime.py, outcomes.py, pdf_colors.py and localization.py provide workload limits, source leases, private aggregate outcomes, bounded colour inspection and initial translation rendering. heatmap.py and channel_preview.py provide ink-limit and separation-mask images. static/index.html contains the UI and shared JSON translation catalog: keep it valid JSON. Both server/browser use that catalog; `data-i18n-html` strings are trusted application markup, never PDF data. Supporting pages are translated static HTML. Current integration scripts are add-seven-languages.py and prepare-main-release.py. Earlier session generators describe historical transformations.

Run `python -m unittest discover -s tests` with Ghostscript available. The release passes 117 tests, including real renderer cases. Tests use owned synthetic PDF fixtures. Older session generators record previous transformations; review their assumptions before replaying them over the current catalog.

## Release

Build the complete backend/static bundle with the Dockerfile. Keep one worker, use a writable temporary volume, and configure request/resource limits and a verified reverse-proxy peer before accepting traffic. The supplied deploy/compose-main.yml is a reference for the resource-bounded service; adapt its image name and loopback port to your environment. Verify the deployed source, TLS route, privacy/cleanup, main PDF workflow and rollback before releasing.

## License

AGPL-3.0; Ghostscript is used under its AGPL license. Source: https://github.com/inkcoverage/inkcoverage.
