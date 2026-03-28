# RPM Artifacts

This directory is intentionally kept source-only in git.

Generated `.rpm` files are **not** committed here. Build outputs go to:

- `dist/packages/*.rpm`

Build command:

```bash
scripts/build_linux_packages.sh
```

To enforce this rule in CI/local checks:

```bash
scripts/check_no_placeholder_artifacts.sh
```
