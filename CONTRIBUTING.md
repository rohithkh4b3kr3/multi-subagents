# Contributing

Thanks for helping. This is a small project; keep changes focused.

- **Run the tests:** `python3 -m unittest discover -s tests -v` (Python 3.11+, standard library only).
- **No new runtime dependencies** for `bin/*`: the tools deliberately use only the Python standard library.
- **No personal data or secrets** in code, tests, comments or docs. Test fixtures must be synthetic.
- **Privacy first:** anything that stores or shares user data needs a clear answer to "what exactly is stored, where, and how
  does the user delete it?" in the PR description and README.
- **Shell and PowerShell:** keep `install.sh` and `install.ps1` in step; both must support a dry run that changes nothing.
- **Honesty in docs:** say what was tested and what was not. Do not quote savings numbers you did not measure.

By contributing you agree your work is licensed under Apache-2.0 (see LICENSE).
