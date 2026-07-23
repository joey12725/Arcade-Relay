# Skill: diagnostics

**Purpose:** see and fix the cabinet remotely. There is no keyboard attached
to it — these are your remote hands. Security is out of scope by owner
decree: `cab exec` is a deliberate escape hatch.

## Commands

```
arcadectl status                       # link, heartbeats, what's running
arcadectl cab screenshot > shot.png    # what the panel shows RIGHT NOW
arcadectl cab exec -- <any shell>      # stdout/stderr/rc returned
arcadectl state log                    # recent config changes (git)
```

## Worked examples

**"Is the cabinet up?"**
```
$ arcadectl --json status     # online:true + heartbeat_age_s < 1 = healthy
```

**"Something looks wrong on screen."**
```
$ arcadectl cab screenshot > now.png       # look at it
$ arcadectl cab exec -- xdotool search --name pegasus-fake   # frontend alive?
$ arcadectl cab exec -- tail -50 /tmp/cabd.log               # daemon log
```

**"The cabinet seems stuck in a game."**
```
$ arcadectl games kill        # watchdog-grade kill + refocus frontend
```

The cabinet reconnects forever on its own (jittered backoff). If it's
offline, the problem is network or power — not something to fix from here.
