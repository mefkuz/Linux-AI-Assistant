# Integration verification

Verification covered the Codex integration, shared clarification tools and voice
answers in a Linux KDE Wayland test environment. All test prompts, filenames and
video references in the committed fixtures are synthetic. Credentials, local
settings, logs, screenshots, recordings and clipboard data are excluded.

## Automated coverage

- 73 unittest tests and the existing 149 script checks passed.
- The same 13 application tool schemas are registered for API and Codex.
- A representative plain-text clarification is routed into the question dialog
  instead of being shown as an ordinary final answer.
- API and Codex clarification answers preserve conversation context and memory.
- Cancelling a question stops dependent actions; prewritten API actions are
  deferred until the model receives the answer.
- Actual Qt dialogs and queued worker callbacks are tested offscreen, including
  choices, free text, cancellation and presentation failures.
- Microphone controls use an icon and asynchronous capture. Transcription is
  editable and never submits itself. Typed text survives failures; closing or
  cancelling discards late results and stops recording.
- Terminal selection, question input, cancellation and EOF handling are covered.
- Missing or hung screen-capture backends fall back to available alternatives;
  private temporary files prevent reuse of stale or concurrent captures.

## Live checks

- An authenticated Codex CLI invoked all 13 application tools. File and shell
  operations used temporary workspaces. Desktop, web and gateway integrations
  used controlled fixtures for the comprehensive model/tool test.
- Real Codex and a configured API both passed answer-driven file creation,
  clarification memory, cancellation without file changes and terminal input.
- A separate real desktop run passed startup, tray, trigger, microphone,
  clipboard, exact text insertion and OCR checks.
- Real microphone cancellation stopped promptly without uploading the recording.
- Speech-service connectivity was checked with generated silent audio.

Optional browser-extension and gateway availability depend on the deployment.
No production endpoint, account identifier, local device identifier or captured
user content is included in this report.

Run automated checks with:

```bash
./venv/bin/python -m unittest discover -s tests -v
```

The scripts in `tests/manual/` are opt-in: live model tests may consume provider
usage, and the desktop smoke test opens a temporary window. They load credentials
locally at runtime; credentials are never embedded in the test sources.

## AI-provided images and user annotations

- API and Codex questions can include local images or a screenshot. Workspace
  and screen-reading permissions apply; attachments never upload automatically.
  AI annotations and raw attachment arguments are rejected.
- Users can draw circles, arrows or boxes on AI-provided images, label them and
  combine them with typed or voice input. They cannot add an image or screenshot
  in the dialog. Only Send submits the reply.
- The native editor supports undo, clear and cancel. Blue marks stay relative
  to the original image when resized; original images remain unchanged.
- The question dialog has no image-mode checkbox or OCR explanation. The large
  editor contains a short vision-model note. Committing marks prepares annotated
  copies; only Send uploads them. Plain text answers do not upload image pixels.
  Image bytes are excluded from conversation history; submitted OCR text can
  enter enabled memory. API image rejection retains OCR/coordinate fallback.
- Screen OCR and a screenshot question reuse the exact same capture, even if OCR
  finds no text. Cached pixels are cleared after answering or screen-changing
  actions and are never shared between requests. Private OCR files are removed.
- Model instructions reserve questions for necessary missing information and
  choices/images for clarifications that need them, avoiding routine prompts.
- Automated tests cover blocked AI annotations, permitted AI images, user editing, explicit
  submission, cancellation, limits, private OCR files and nonvision API fallback.
- Real Codex and the configured API previously passed synthetic user-annotation
  replies in OCR-only and image modes. The manual tests use model-provided synthetic images and user-only annotations.

Opt-in synthetic test (provider usage applies):

```bash
./venv/bin/python tests/manual/test_annotated_answers_live.py
```

## Release upgrade simulation

- v1.6.0 → v1.7.0 detection, release notes, Later, Skip and manual override
  passed with real Qt dialogs offscreen.
- A background install result reaches the GUI and opens the restart prompt.
  Settings background checks also update their status on the GUI thread.
- A real fast-forward pull in temporary local repositories installs the new
  fixture while preserving ignored settings/logs. Already-current, conflicting
  local edits and ZIP installation paths were exercised. pip invocation was
  recorded without installing packages or using the network.
- Preview PNGs and report.json are generated outside the repository by
  tests/manual/simulate_update.py; a regression test runs the same simulation.
