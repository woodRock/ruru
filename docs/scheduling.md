# Scheduling `ruru fetch`

ruru has no daemon. Run `ruru fetch` on a schedule with whatever you already use. Each run is
safe to repeat: it only adds items that are not already in the library, the inbox or
`seen.jsonl`, and it commits them in one go. A feed that fails (exit 3) is retried from where it
left off on the next run.

Schedulers start with a minimal environment, not your shell profile, so use the full path to
`ruru` (`which ruru`; with `uv tool install` it is `~/.local/bin/ruru`) and pass the library
explicitly with `-L` if it isn't the default `~/papers`.

## launchd (macOS)

`~/Library/LaunchAgents/nz.ruru.fetch.plist` (replace `/Users/you` with your home):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>nz.ruru.fetch</string>
  <key>ProgramArguments</key>
  <array>
    <string>/Users/you/.local/bin/ruru</string>
    <string>fetch</string>
  </array>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>7</integer><key>Minute</key><integer>0</integer></dict>
  <key>StandardOutPath</key>   <string>/Users/you/papers/.ruru/fetch.log</string>
  <key>StandardErrorPath</key> <string>/Users/you/papers/.ruru/fetch.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/nz.ruru.fetch.plist     # start
launchctl start nz.ruru.fetch                                  # run once now, to test
launchctl unload ~/Library/LaunchAgents/nz.ruru.fetch.plist   # stop
```

launchd runs a missed job when the Mac wakes, so a laptop that sleeps at 07:00 still fetches.

## cron

```cron
0 7 * * * $HOME/.local/bin/ruru fetch >> $HOME/papers/.ruru/fetch.log 2>&1
```

## A Claude scheduled task (fetch and triage)

With the ruru skill installed (see the README), a scheduled Claude Code task can fetch and
pre-triage, so your inbox has a recommendation on every item. A weekly prompt:

> Use the ruru skill's weekly triage: run `ruru fetch`, read `ruru inbox --json`, and run
> `ruru suggest` on every item with a one-line reason tied to my projects. Don't keep, skip or
> cite anything. Finish with a short summary of the items you suggest keeping.

Then `ruru inbox --suggested keep` shows the picks, and
`ruru inbox -q --suggested skip | xargs ruru skip` clears the rest once you agree.

`.ruru/` is git-ignored, so the log file never ends up in the library's history.
