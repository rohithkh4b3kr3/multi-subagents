# Third-party tools

This repository contains **no code from these projects**. `install.sh` / `install.ps1` download and install
them from their own sources onto your machine, at your request. Each stays under its own license, and you
are responsible for complying with it. Check the current terms in each repository before relying on this table.

| Tool | Source | License | Notes |
|---|---|---|---|
| rtk | https://github.com/rtk-ai/rtk | Apache-2.0 | |
| code-review-graph | https://github.com/tirth8205/code-review-graph | MIT | |
| token-savior | https://github.com/mibayy/token-savior | MIT | |
| graphify | https://github.com/Graphify-Labs/graphify | Apache-2.0 | |
| context-mode | https://github.com/mksglu/context-mode | Elastic License 2.0 | You may use it, but you may not offer it to third parties as a hosted or managed service. Do not bundle it into a paid product or service. |
| caveman | https://github.com/JuliusBrussee/caveman | MIT (skill) + Business Source License 1.1 (engine) | Self-hosted use for your own traffic is allowed. Offering its functionality to third parties as a hosted, managed or embedded service needs a commercial license from its author. |

If you fork this project for a commercial or hosted offering, keep context-mode and caveman as opt-in
installs from upstream, or obtain the licenses you need. Skip either one with `--skip ctx` / `--skip cave`
(`-Skip ctx` / `-Skip cave` on Windows).

## Optional third-party services

None are used. `token-dashboard` makes no external requests; `token-history` and `token-report` are local.
