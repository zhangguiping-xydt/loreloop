# Releasing LoreLoop

1. Confirm the working tree is clean and update `CHANGELOG.md` and the version
   in `pyproject.toml`. Regenerate dependencies explicitly from public PyPI:
   `UV_INDEX_URL=https://pypi.org/simple uv lock --refresh`, then verify the
   lockfile registry test passes.
2. Run `uv sync --frozen --all-extras`, then use `uv run --frozen` for the full
   test/coverage suite, security
   checks, deterministic evaluation validation, `uv build`, and
   `uv run twine check dist/*`.
3. Create a signed annotated tag matching the package version, for example
   `git tag -s v0.1.0 -m "LoreLoop 0.1.0"`, then push the tag.
4. The pinned release workflow rebuilds and tests the distributions, generates
   an SPDX SBOM, creates a GitHub artifact attestation, publishes through PyPI
   Trusted Publishing, and creates the GitHub release.
5. Verify the PyPI provenance, install into a fresh environment, run
   `loreloop --help` and `loreloop demo --offline`, then publish release notes.

Repository administrators must first create the protected `pypi` environment
and configure the matching PyPI Trusted Publisher. Long-lived PyPI API tokens
must not be stored in repository secrets.
