# Agent Guide: Publishing to PyPI

This document instructs AI agents and developers on how to release new versions of the packages in this repository (`vibe-ocr` and `vibe-logger` (vibe-colored-logger)).

The repository uses GitHub Actions to automatically build and publish packages to PyPI when a specific git tag is pushed.

## Supported Packages

1.  **vibe-ocr**
    *   Directory: `./vibe-ocr/`
    *   Tag Pattern: `vibe-ocr-v*` (e.g., `vibe-ocr-v0.1.2`)
2.  **vibe-colored-logger**
    *   Directory: `./vibe-logger/`
    *   Tag Pattern: `vibe-logger-v*` (e.g., `vibe-logger-v0.1.7`)

## Release Workflow

To publish a new version, you must follow these exact steps. **Do not skip the tagging step.**

### 1. Update Version Number

Read the `pyproject.toml` file of the target package to find the current version, then increment it.

*   **vibe-ocr**: Edit `vibe-ocr/pyproject.toml`
*   **vibe-logger**: Edit `vibe-logger/pyproject.toml`

**Tool Usage:**
Use `replace` tool to update the `version = "x.x.x"` line in `pyproject.toml`.

### 2. Commit Changes

Commit the `pyproject.toml` change to the repository.

```bash
git add vibe-ocr/pyproject.toml  # or vibe-logger/pyproject.toml
git commit -m "chore: bump vibe-ocr version to X.X.X"
git push origin main
```

### 3. Create and Push Tag (Trigger)

**CRITICAL:** The PyPI publication workflow is *only* triggered by pushing a specific tag.

*   For **vibe-ocr**: `vibe-ocr-v{VERSION}`
*   For **vibe-logger**: `vibe-logger-v{VERSION}`

**Example:**
If you bumped `vibe-ocr` to `0.1.3`, run:

```bash
git tag vibe-ocr-v0.1.3
git push origin vibe-ocr-v0.1.3
```

**Example:**
If you bumped `vibe-logger` to `0.1.8`, run:

```bash
git tag vibe-logger-v0.1.8
git push origin vibe-logger-v0.1.8
```

## Verification

After pushing the tag, the corresponding GitHub Action will start.
- `pypi-vibe-ocr.yaml` listens for `vibe-ocr-v*`
- `pypi-vibe-colored-logger.yaml` listens for `vibe-logger-v*`
