# Five-minute legacy upload loop

From the knowhelm source checkout, run:

```bash
knowhelm demo --agent codex
```

The script creates an isolated Git repository and visibly executes the complete
`init -> ingest -> run -> verify -> report -> harvest` loop. It uses the real
selected agent and a real local browser verification; install the release with
the `web` extra and Chromium first.

For a credential-free plumbing check (the mode used on Linux, macOS, and Windows
CI), run:

```bash
knowhelm demo --offline
```

Offline mode is not an agent-quality benchmark. It replaces only the external
agent and browser adapters with deterministic local implementations, while using
the real knowledge store, retrieval, evidence chain, reporting, and harvest code.

`python examples/legacy-upload/demo.py ...` is an equivalent thin entry point
for contributors inspecting the source checkout.
