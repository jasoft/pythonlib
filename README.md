# Python Libraries

This repository contains shared Python libraries used in Vibe projects.

## Packages

- **vibe-logger**: A colored logger with context injection support.
- **vibe-ocr**: A decoupled OCR helper library using PaddleOCR.

## Publishing

This repository is configured to publish packages to PyPI automatically when a GitHub Release is published.

### Prerequisites

1.  **PyPI Account**: Ensure you have an account on PyPI.
2.  **API Token**: Generate an API token on PyPI (scoped to these projects or your user).
3.  **GitHub Secret**: Add the token to this repository's secrets as `PYPI_API_TOKEN`.

### Workflow

1.  Update the `version` in `pyproject.toml` for the package you want to release.
2.  Commit and push the changes.
3.  Create a new Release on GitHub.
4.  The GitHub Action will build both packages and upload them to PyPI. (It will skip versions that already exist).
