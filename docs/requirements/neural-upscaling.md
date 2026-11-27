# Neural upscaling

Code: the "Neural upscaling" row and the "Draw a page" flow of the AGENTS.md Roadmap.

## When a page is upscaled

- Off by default (`neural upscaling`).
- It runs only on a GPU. It never runs on the CPU, in whole or in part, and never falls back to
  it. Without a usable GPU runtime the feature is unavailable and pages are drawn as before.
- A page qualifies only when its longest edge is below `neural upscaling max input size` and it
  is a static page without transparency. Very small and very large pages never qualify, whatever
  the preference says. Animations are never upscaled.
- A qualifying page is upscaled only when it is shown larger than its original size, unless
  `neural upscaling always` is on.

## Drawing

- Drawing never waits for the model. Until a result is ready, and whenever anything fails, the
  page is drawn the conventional way; when the result arrives for a page still shown, the page
  is redrawn with it.
- While a page's result is kept, changing zoom or fit mode reuses it: a conventional resize of
  the result gives the exact size the zoom mode asks for.
- Layout, 100% zoom, rotation and the reported resolution use the original page's size.
- A grayscale page stays grayscale: its result is never tinted.
- The upscaled results kept in memory are bounded; a result too large to keep is not computed.

## Model and runtime

- The model is the bundled default unless `neural upscaling model` names another file.
- `neural upscaling runtime` set to `auto` uses the faster Windows ML provider when one works,
  and DirectML otherwise; `directml` always uses DirectML.
- A Windows ML provider is downloaded only when the user asks to install it, never while
  reading.
- An engine compiled for a provider is stored under the app's data folder and reused on later
  runs. Quitting doesn't wait for an engine that is being built, and a build that crashes or
  hangs doesn't take the viewer down.
- When the Windows ML provider fails while upscaling, the page is redone with DirectML, and
  DirectML is used until upscaling is switched off and on, the model or the runtime changes, a
  provider is installed, or the app restarts.
- A Windows ML engine that fails to build or load (a crash or a timeout) is remembered for that
  model and provider version: later runs use DirectML without building it again. A build cut off
  by quitting is not remembered and is built again.
- A model that fails on DirectML turns upscaling off; it is not retried on every page. It is
  tried again when the user switches upscaling off and on, changes the model or the runtime,
  installs a provider (used from the next upscaled page, without a restart), or restarts the app.
- The model is released after a period without requests and loaded again when needed.
