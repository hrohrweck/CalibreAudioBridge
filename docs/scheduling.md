# Scheduling CalibreAudioBridge

`cab run` is designed for unattended scheduled execution: it takes a single-instance
lock, honors a time budget, and resumes interrupted work on the next run.

## cron

```
# Daily at 03:00, 4h budget, log to the state dir
0 3 * * *  /usr/local/bin/cab run --time-budget 4h >> ~/.local/state/calibreaudiobridge/cron.log 2>&1
```

Exit codes (useful for cron alerting):

| Code | Meaning |
|---|---|
| 0 | all candidate work done (or nothing to do) |
| 1 | some books failed (inspect `cab status`, requeue with `cab retry`) |
| 2 | environment/config error (missing binary, bad config, lock held) |
| 3 | calibre library blocked: GUI/server running and no Content Server reachable |

## launchd (macOS)

`~/Library/LaunchAgents/com.calibreaudiobridge.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>com.calibreaudiobridge</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/local/bin/cab</string>
    <string>run</string>
    <string>--time-budget</string><string>4h</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>3</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key><string>/tmp/cab.launchd.log</string>
  <key>StandardErrorPath</key><string>/tmp/cab.launchd.log</string>
</dict>
</plist>
```

Enable with `launchctl load ~/Library/LaunchAgents/com.calibreaudiobridge.plist`.
Unlike cron, `StartCalendarInterval` wakes the Mac if asleep (otherwise the run
simply resumes where it left off — interrupted work is never lost).

## Notes

- **Overlap safety**: a second `cab run` while one is active exits immediately
  with code 2 (flock on `<work_dir>/lock`).
- **Sleeping Macs**: runs killed by sleep or shutdown resume at the exact
  sentence/chunk on the next invocation (TTS cache + ledger).
- Keep `max_books_per_run` small (default 3) so one run completes books fully
  instead of leaving many half-rendered.
