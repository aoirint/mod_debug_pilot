# System design

This design depends on the [target platform contract](../domain/target-platform.md).

## Status

The content below is the approved design target. Source implementation is not
present in the foundation commit.

## Dependency direction

```text
entrypoint -> composition -> ui -> presentation -> application -> domain
                              \                 /
                               infrastructure --
```

Domain values and application jobs do not import Flet or concrete filesystem
services. Presentation owns immutable view state. Flet controls render that
state and emit intents. Infrastructure owns settings, file copies, process
lifecycle, screenshot capture, and artifact persistence.

## Job lifecycle

A single page-session task owns an active job. Duplicate starts are rejected.
Cancellation increments the request generation, terminates the process tree,
collects truthful partial artifacts, and prevents stale completion from
replacing newer state.

Supported jobs are `validate_environment` and `run_smoke_test`. No data model
contains a shell command or download URL.

## Data classes

Configuration is public local data: game path, base-profile path, mod DLL path,
artifact root, timeout, resolution, ready marker, and screenshot delay. It is
written atomically to the platform application-data directory. It must contain
no credentials. Each run has an immutable request snapshot and a unique
artifact directory containing request, environment, result, logs, and images.

Update this document when state ownership, dependency direction, allowed jobs,
or persistence contracts change.
