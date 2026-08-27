# s3-to-gdrive

Weekly sync of the TES seamless-config files from the four production S3 buckets into the
**TES-All Country Configs** Google Drive folder, followed by a refresh of the *Config
Coverage per Country* table on the [TES PDT Display Format Confluence page](https://deliveryhero.atlassian.net/wiki/spaces/LOGCPL/pages/1730215943).

## How it works

The workflow is an **MCP-driven skill**, not a script. Claude runs it and does all I/O
through connected MCP servers:

| Step | MCP server | Tools |
| --- | --- | --- |
| List and download S3 objects | AWS API MCP | `call_aws` — `s3api list-objects-v2`, then `s3 cp <key> -` per file (`s3 sync` is attempted first but usually rejected, see below) |
| Create folders, upload files | Google Drive MCP | `search_files`, `create_file`, `download_file_content` |
| Refresh the coverage table | Atlassian MCP | `getConfluencePage`, `updateConfluencePage` |

[`SKILL.md`](SKILL.md) is the authoritative procedure — bucket list, Drive folder ID,
Confluence page ID, phase-by-phase steps, and guardrails.

The only local code is [`scripts/analyze_configs.py`](scripts/analyze_configs.py), which
does pure computation: parse `default.yml` files, diff against the previous snapshot, and
render the Confluence table HTML. It performs no network I/O and touches no credentials,
so it can be run and tested on its own.

## Prerequisites

1. **A configured AWS CLI with a live SSO session.** The AWS MCP server drives your local
   AWS CLI, so it needs the CLI installed and `~/.aws` set up, and it inherits that SSO
   session. Refresh it with:
   ```bash
   aws sso login
   ```
2. **AWS, Google Drive and Atlassian MCP servers connected** in Claude.

No longer needed, as of the MCP rewrite: `gcloud` and application default credentials,
`~/.s3gdrive_credentials.json`, `~/.s3gdrive_token.json`, `CONFLUENCE_EMAIL`,
`CONFLUENCE_API_TOKEN`, and the `google-api-python-client` / `google-auth` / `requests`
packages. The AWS CLI is still required, but only because the MCP server calls it — you
never invoke it yourself except to log in.

## Running it

**Scheduled:** the `s3-to-gdrive-weekly-sync` task runs it every Monday at 10:05 local time.

**On demand:** ask Claude to *"run the TES config sync"*.

**Just the analysis**, against the local config mirror:

```bash
pip install pyyaml --break-system-packages
python3 scripts/analyze_configs.py --root .cache/configs \
  --snapshot config-snapshot.json \
  --out-json .s3sync/summary.json \
  --out-html .s3sync/table.html
```

Add `--write-snapshot` to record the result as the new baseline. Exit codes: `0` fine,
`1` no configs found, `2` bad arguments, `3` the country set shrank past the safety
threshold (see below).

## State files

| File | Purpose | Committed |
| --- | --- | --- |
| `manifest.json` | `bucket/key` → ETag, size, Drive file ID. Drives the "has this changed?" check so unchanged files are never re-uploaded. | Yes |
| `config-snapshot.json` | Last known per-country config summary. Drives the change diff and the shrink guard. | Yes |
| `reports/sync-<date>.md` | One report per run. | Yes |
| `reports/backups/page-<date>.html` | Confluence page HTML as it was immediately before each push — the revert path. | Yes |
| `.cache/configs/` | Persistent local mirror of the config files. Guarantees the analysis always sees the complete country set. | No |
| `.s3sync/` | Per-run scratch. | No |

Both state files are committed on purpose — they are what makes the sync incremental.
Delete `manifest.json` and the next run does a baseline reconciliation against Drive
(slower, but it will not duplicate anything).

`config-snapshot.json` was seeded from the 74 country rows on the Confluence page as of
2026-06-17, so the first real run reports genuine drift rather than 74 spurious additions.

### The shrink guard

The Confluence table is rebuilt wholesale from whatever `default.yml` files the analysis
finds, so an incomplete parse root would silently replace the full country table with a
partial one. `analyze_configs.py` refuses to emit any HTML if more than 10% of the
countries in the snapshot have gone missing, exiting `3` instead. If that fires, the fix is
almost always to fetch the missing config files — not to pass `--allow-shrink`, which
disables the protection.

## Known constraints

- **AWS SSO cannot be refreshed unattended.** `aws sso login` needs browser approval, so
  if the session has expired when the scheduled run fires, the run reports `BLOCKED` and
  does nothing else. Run `aws sso login` and re-run it manually. The durable fix is to
  move the sync to CI with an IAM role via OIDC — see below.
- **The Drive MCP has no update or delete tool.** It exposes only `create_file`,
  `copy_file` and read tools. A changed file is therefore uploaded alongside the old copy
  rather than replacing it; each run lists any such stale copies in its report so they can
  be pruned by hand. Unchanged files are skipped entirely, which is what keeps duplicates
  from accumulating.
- **Text only.** File contents move through tool responses as UTF-8. These buckets hold
  YAML, so this is fine in practice; any non-UTF-8 object is skipped and flagged.
- **The hand-maintained top of the Confluence page is never rewritten.** Only the content
  below the *Config Coverage per Country* heading is regenerated, the pre-update page HTML
  is saved to `reports/backups/` first, and the run aborts rather than pushing a table with
  fewer rows than the page already has.

## Possible next step: move to CI

Because both AWS SSO and the MCP servers depend on an authenticated local session, this
workflow cannot run fully unattended. A GitHub Actions job with an IAM role via OIDC for
S3 and a Google service account with domain-wide delegation for Drive would remove the
local-machine dependency altogether — at the cost of reintroducing a Python client for
Drive and Confluence.
