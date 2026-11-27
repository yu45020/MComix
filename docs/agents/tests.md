# Tests

AGENTS.md Roadmap ("Tests") says where the tests, shared fakes and fixtures are.

- Tests assert behavior the user or a requirement (`docs/requirements/`) relies on, not just what
  the code currently does.
- Test each behavior once, at its cheapest public entry point: the function or method the rest of
  the app calls (an archive backend's `iter_contents` or `extract`, `FileHandler.open_file`, a
  preference read through `prefs`), not the dialog above it.
- Don't test private helpers or mock the behavior under test. Fakes stand in for what a test can't
  have: the main window around a handler (`test/_app_fakes.py`, `test/_page_fakes.py`), an archive
  backend (`test/_archive_fakes.py`), external tools and the GPU runtime, password prompts
  (`test/_passwords.py`) and the clock. Wrapping an internal
  step to inject a failure or hold a race open is fine while the real code still runs.
- No timing assertions. Measurements follow [validation.md](validation.md).
- Tests build what they need in a temporary folder, or read the small fixtures in `test/files/`.
  They never read `sample_data/`, the owner's `%APPDATA%\MComix`, or real comic folders, and never
  write inside the repo.
- A test that needs an external tool (7z) or an optional package (PyMuPDF, ONNX Runtime,
  torch) skips when it is absent, as the existing ones do.

## Adding a test

- A fix gets a regression test that fails on the old code.
- A feature gets a test for each acceptance criterion with no coverage, after searching for an
  existing test of that behavior; prefer a new `parametrize` case or an extended test.
- An edge state ([review.md](review.md) "Edge states") gets a test when the diff creates or reaches
  it and you can trace the failure, not for every state on the list.
- Don't add tests for unchanged behavior or for each branch of the implementation. Run a test
  written to check your own understanding, then delete it.
- Never test a library or GTK directly (each image format Pillow decodes, say). A library behavior
  the app relies on is covered through the app's own entry point.

## Changing a test

- A test that fails after a change that keeps behavior pins the implementation (Removing a test,
  rule 2): rewrite it to assert the behavior instead of adapting it to the new internals.
- A deliberate behavior change updates its tests in the same diff.
- Never loosen an assertion or add a skip or `xfail` to make a test pass. Fix the code, or record
  the disagreement as an Open item.
- A flaky test is fixed, never retried, skipped or rerun until it passes; one the diff doesn't touch
  becomes an Open item ([validation.md](validation.md)).

## Removing a test

- **On touch:** a diff removes the tests it makes obsolete: their behavior was removed. Other trims
  you notice become Open items.
- **In bulk:** the owner starts a trim run on demand ([test-trim.md](../review/test-trim.md)), over
  the whole suite or the tests added or changed since a commit.

A test, a step of it, or a parametrize case is a trim when it:

1. **Duplicates** a kept test: same behavior, same or a more expensive entry point. Keep the test at
   the cheapest public entry point.
2. **Pins implementation:** private helpers, call counts, log or message text nothing relies on,
   internal data shapes.
3. **Mocks the behavior under test**, or asserts what can't fail or what a library or GTK does.
4. **Tests branches, not behavior:** several tests or cases in one equivalence class. Keep one case
   per class. Separate tests of distinct classes may fold into one `parametrize` when that removes
   lines.
5. **Guards an edge state or a fixed bug that another test already covers**, or one the code can no
   longer reach or reintroduce (trace it).
6. **Duplicates setup:** inline scaffolding that an existing `test/_*.py` fake or `conftest.py`
   helper provides. A new helper is a trim only when it removes more lines than it adds.

Under every rule, the only test of a fixed bug the code can still reintroduce, or of a reachable
edge state that loses or corrupts the user's files or settings, is never a trim. When such a test
pins implementation (rule 2), it becomes an Open item to rewrite it at a public entry point.
