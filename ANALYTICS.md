# Optional usage measurement

Google Analytics loads only after affirmative permission on the production hostname. Declining leaves every tool available. A localized Analytics choices control can withdraw permission; withdrawal disables collection, clears GA cookies and reloads to remove the loaded tag. Beta/local pages and the built-in test PDF do not enter production tool-use funnels. The choice and Analytics cookies last up to180 days.

The browser records accepted PDF uploads (`file_upload`), user-confirmed processing requests (`analysis_requested`, `ink_limit_requested`) and separate completed/failed outcomes. Upload and request events are key events; reading the result is normal successful use and requires no export. A batch counts one request, with a complete/partial/failed/cancelled outcome rather than per-page conversions. Native server aggregate operation counters remain separate.

The analytics helper accepts only fixed event names and categorical metadata. It strips query strings/fragments from page locations and paths/queries from referrers. It never accepts filenames, document IDs, page counts, channel names, geometry, user-entered text or calculated results. Advertising signals are disabled. Automatic forms, site-search queries, file-download events and browser-history page views are disabled in the production stream to prevent unwanted metadata and duplicate page views. Scroll/outbound-click measurement remains separate from product outcomes.

Run `node --test tests/analytics*.mjs` for consent/privacy-boundary and real workflow-function regressions. The Python regression suite still checks renderer behavior, private responses and no server-set cookies. No real customer document or live contact submission is needed for these tests.

Released on22 October2026. All117 existing Python regressions and12 Node consent/workflow checks pass. All35 public routes and the changed assets match the release. Live owned-PDF upload, single/batch analysis and ink-limit result events were observed in GA4 DebugView. No customer file or contact submission was used.
