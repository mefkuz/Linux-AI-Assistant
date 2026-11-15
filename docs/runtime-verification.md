# Integration verification

Verification covered the Codex integration, shared clarification tools and voice
answers in a Linux KDE Wayland test environment. All test prompts, filenames and
video references in the committed fixtures are synthetic. Credentials, local
settings, logs, screenshots, recordings and clipboard data are excluded.

## Automated coverage

- 52 unittest tests and the existing 149 script checks passed.
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
