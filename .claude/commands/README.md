# Slash Commands — Quick Reference

Pick the command that matches your situation right now.

## Starting Work

| I want to... | Command | What happens |
|--------------|---------|-------------|
| Work on a specific subsystem | /context | Asks which area, loads focused context dump |
| Dive into writeback specifically | /writeback | Loads all writeback docs + code, asks what to build |
| Start a new feature from scratch | /feature | Helps plan: affected apps, files to create, order |

## During Work

| I want to... | Command | What happens |
|--------------|---------|-------------|
| Check my changes follow conventions | /review | Runs git diff + ruff, checks against coding rules |
| Run tests and fix failures | /test | Runs pytest, reads failures, proposes fixes |

## Something's Wrong

| I want to... | Command | What happens |
|--------------|---------|-------------|
| Debug a bug | /debug | Loads recent changes + docs, asks for bug description |
| Check if my environment is healthy | /health | Checks Docker, Ollama, DB, migrations, linter |

## The Daily Flow

```
Morning start:     /health  (is everything running?)
Pick a task:       /context  or  /writeback  or  /feature
While coding:      /review  (am I following conventions?)
Something breaks:  /debug
Before committing: /test  then  /review
```

## Rules of Thumb

- Starting a session? Always /context or /writeback first
- Wrote more than 50 lines? Run /review
- About to commit? Run /test then /review
- Error you don't understand? /debug
- First time today? /health