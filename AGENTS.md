# TraceCoder development instructions

- Keep TraceCoder dependency-free at runtime and compatible with Python 3.10+.
- Preserve workspace confinement, secret protection, command safety, and auditable run artifacts.
- Prefer small, focused changes over framework-level rewrites.
- Run `python3 -m unittest discover -s tests -q` after changing Python code.
- Never commit `tracecoder.local.json`, API keys, `.runs/`, caches, or generated artifacts.
- Public releases contain project code and documentation only; local tests and development history stay private unless explicitly requested.
