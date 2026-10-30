# Phase 4: library search interface

Implemented locally, September 16, 2026. The My library view now has an opt-in
“Search my videos” panel using the phase 3 authenticated `POST /search` endpoint.
It searches uploads in hybrid mode without exposing engine choices. Public explore
does not show this control. Existing within-video search remains available.

Results show the current video title, literal transcript text, and a timestamp.
Selecting a result opens the existing authorized watch flow, waits for media
metadata, and seeks to the passage. It does not force autoplay. Selecting another
video clears the previous player and guards against late status, transcript,
metrics, and search responses replacing the new selection's content.

The panel distinguishes loading, complete empty results, partial/degraded empty
results, indexing in progress, terminal indexing failure, service failure, busy
search, expired authentication, disabled search, and the 100-video scope bound.
409/503 policy responses retain their notices instead of becoming generic fetch
errors. Search submissions cancel older fetches and reject stale responses. Text
is rendered through React, without inserting backend HTML. Input labels, keyboard
submission, focusable result buttons, and live status announcements are included.

## Activation

Both flags remain opt-in:

- API: `LIBRARY_SEARCH_ENABLED=true`, after phase 2 indexing setup and publication.
- Frontend build: `VITE_LIBRARY_SEARCH_ENABLED=true`; rebuild the frontend when
  changing this flag. See `frontend/.env.example`.

The Vite development proxy includes `/search`. The final local build was restored
to the feature-disabled configuration after validating the enabled build. No
production service, application database, or real user video was changed.

## Validation

- TypeScript check passed.
- Enabled and disabled production Vite builds passed using `--config vite.config.ts
  --configLoader native`; native loading avoids this machine's sandbox restriction
  on esbuild's parent-directory scanning during configuration bundling.
- `tests/library_search_browser.cjs` passed in headless Microsoft Edge against the
  actual built frontend, with API and media fixtures. It checks success, partial
  results, indexing and terminal failure, no matches, outage, overload, disabled
  endpoint, safe text rendering, 390px mobile overflow, navigation to a 12-second
  passage, retained within-video search, and public-feed exclusion.
- Desktop and mobile screenshots were inspected. They are local fixtures in
  `tmp/phase4-library-desktop.png` and `tmp/phase4-library-mobile.png`.

Reproduce with a locally installed Playwright (or `PLAYWRIGHT_MODULE` pointing to
its package), Microsoft Edge, and an enabled frontend build served at
`http://127.0.0.1:5174`:

```sh
node tests/library_search_browser.cjs
```

Use `VINDEX_UI_URL` to select another local preview. Set
`VINDEX_EXPECT_SEARCH_DISABLED=true` to verify the disabled build hides the panel.
These browser tests validate UI integration against fixtures; they do not establish
live model quality, end-to-end production latency, or remote storage behavior.

Phase 5 remains: fixed-corpus relevance comparisons, failure/load measurements,
observability completion, and a reviewed rollout. The unfinished baseline must be
completed before claiming Elasticsearch improves retrieval or performance.
