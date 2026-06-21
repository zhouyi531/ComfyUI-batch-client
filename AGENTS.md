<!-- BEGIN services-hub registry (auto-added) -->
# Service Registry Hub — http://localhost:1234

**Purpose.** This machine runs a central **Service Registry Hub** at
`http://localhost:1234`. It is the single place that records every service
running on this box during development: where each service's code lives
(`code_path`), its port, how to call it (`constraints` / `endpoints`), what env
it needs, and how to start/restart it. It exists so an AI agent can **find &
integrate** an existing service, or **register** one it just built, without a
human re-explaining each time.

It is **self-documenting**: `GET http://localhost:1234/` returns full, current
usage instructions. When in doubt, fetch that first.

> Use the hub whenever a task touches a service that other parts of the system
> depend on (e.g. "integrate the `stt` service", "expose this as a service").

## Find / use a service
1. `GET http://localhost:1234/services/<name>` — full record (code_path, port,
   `base_url`, endpoints, constraints, env_required).
   - Don't know the exact name? `GET http://localhost:1234/search?q=<term>` or
     list everything with `GET http://localhost:1234/services`.
2. Read `constraints` and `endpoints`, then integrate against `base_url`.
3. Check it's up: `GET http://localhost:1234/services/<name>/status`.
   If it's down: `POST http://localhost:1234/services/<name>/start`.

## Register a service (after building or changing one)
1. `GET http://localhost:1234/schema` — exact fields + a full example.
2. Gather accurate values **from the repo**: `code_path` (absolute), `port`,
   `protocol`, the real `lifecycle.start` command, a `health_check` URL, and the
   calling `constraints` (formats, size/rate limits, auth, gotchas), plus
   `endpoints`, `env_required`, `dependencies`.
3. POST it (upsert by `name`):

```bash
curl -s -X POST http://localhost:1234/services \
  -H 'Content-Type: application/json' -d @service.json
```

4. Verify: `GET http://localhost:1234/services/<name>`.

## Unregister a service
```bash
# stop it first if the hub manages its process:
curl -s -X POST   http://localhost:1234/services/<name>/stop
curl -s -X DELETE http://localhost:1234/services/<name>
```

## Lifecycle control (optional)
- `POST http://localhost:1234/services/<name>/start` (add `?wait=5` to wait for health)
- `POST http://localhost:1234/services/<name>/stop`
- `POST http://localhost:1234/services/<name>/restart`
- `GET  http://localhost:1234/services/<name>/status`
- `GET  http://localhost:1234/services/<name>/logs?lines=200`

The node self-describes at `GET http://localhost:1234/` — fetch it when unsure.

<!-- END services-hub registry -->
