# Hotstar browser-extension folder (placeholder)

`Akbots/hotstar_browser.py` looks in this folder for an **unpacked**
Chrome/Chromium extension (a `manifest.json` at the top level of this
folder, plus whatever `.js`/`.json`/icon files it references) and loads
it with `--load-extension` when capturing a Hotstar stream's MPD +
Widevine license URL through a real browser session.

## What to put here

Unzip the extension exactly as-is into this folder, so you end up with:

```
Akbots/hotstar_extension/
  manifest.json
  ...whatever else the extension ships with...
```

Nothing else needs to change — `Akbots/hotstar_browser.py` checks for
`Akbots/hotstar_extension/manifest.json` at call time and picks it up
automatically the next time `/hotstar`'s browser fallback runs. No
restart-required config, no path to edit.

## If this folder is left empty (default)

`hotstar_browser.py` doesn't fail — it just falls back to pure network
interception with no extension loaded at all (same as if extension
loading had errored out), same as this project's other Playwright-based
fallback in `Akbots/headless.py`. The MPD/license capture still works
via `page.on("request", ...)`; the extension, if/when supplied, is
purely an *additional* capture path, not a required one.

## Why an extension at all, if network interception already works?

Playwright's own `page.on("request", ...)`/`context.on("request", ...)`
hooks (which `hotstar_browser.py` already uses) see everything that goes
over the network regardless of an extension — for plain MPD-manifest and
license-request URLs that's normally enough on its own. An extension is
only worth adding on top of that if it does something Playwright's network
hooks can't — e.g. reading values out of the page's own JS runtime
(`EME` session internals, in-memory tokens that never touch the network
as a plain URL, etc.). If your extension is that kind of tool, drop its
unpacked files here; if it's just another way of grabbing the same MPD/
license URLs, the built-in network interception already covers it and
you don't need to add anything here at all.

## One technical note

`--load-extension` only works with a real Google Chrome/Chromium binary
on PATH (`google-chrome`, `google-chrome-stable`, `chromium`, or
`chromium-browser`) — Playwright's own bundled Chromium build silently
ignores that flag. `hotstar_browser.py`'s `find_chrome_for_extensions()`
looks for one of those automatically; if none is installed on this host,
the extension is skipped (with a log line saying so) and it falls back
to the no-extension path instead of erroring out.
