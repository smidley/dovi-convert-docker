# v1.2.2 – Single-line progress bar

## Fixes
- **Progress bar in the output:** The `dovi_convert` spinner is now shown as one continuously updated line with a progress bar, percent, step, elapsed time and ETA, e.g. `[████████████████░░░░░░░░]  66%  Step 1/3: Extracting video stream  ⏱ 1m 05s  ETA 45s`. Finished steps stay in the log as normal lines. Percent and ETA are estimates (dovi_convert reports no percentage).
- **Responsiveness:** ANSI control codes of the spinner are stripped and updates are throttled, so the UI no longer lags during long conversion steps.

## Upgrade notes
- No configuration changes needed. Pull the new image: `docker pull jpkrny/dovi-convert:1.2.2`

## Full changelog
See [CHANGELOG.md](CHANGELOG.md).
