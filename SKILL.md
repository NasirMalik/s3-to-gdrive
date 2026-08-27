---
name: tes-config-sync
description: Sync TES seamless-config files from the four production S3 buckets to Google Drive and refresh the Config Coverage per Country table on Confluence. Use when asked to run the TES config sync, the S3-to-Drive sync, or the weekly config sync.
---

# TES config sync — S3 → Google Drive → Confluence

Runs entirely through MCP tools. Nothing is executed from this repo except one
pure-computation helper; there is no CLI for you to invoke, no gcloud, no OAuth JSON and
no Confluence API token.

**Repository:** `/Users/n.mahmood/Ai-directory/DH-Repos/s3-to-gdrive` (referred to as `<repo>`)

## Constants

| Item | Value |
| --- | --- |
| S3 buckets | `production-ap-…`, `production-eu-…`, `production-kr2-…`, `production-us-…`, each suffixed `-customer-logistics-seamless-configs-tes` |
| Drive parent folder | `1XLm6DJH-4-jgtv77w6mQyV6EYy9SjNVd` ("TES-All Country Configs") |
| Confluence cloudId | `89647aba-aaa1-4669-9f6d-a9ad8db6435e` (deliveryhero) |
| Confluence page | `1730215943` — *PDT Display Format per Platform — Single Value vs Range*, space `LOGCPL` |
| Section marker | the `<h2>` whose text is **Config Coverage per Country** |
| Config corpus | `<repo>/.cache/configs/<bucket>/<key>` — persistent local mirror, gitignored |
| Scratch | `<repo>/.s3sync/` — per-run, deletable |
| State | `<repo>/manifest.json`, `<repo>/config-snapshot.json` |
| Page backups | `<repo>/reports/backups/` — **not** gitignored, never auto-deleted |

## Tools

Load everything in one call:

```
ToolSearch "select:mcp__AWS_API_MCP_Server__call_aws,mcp__08ee302d-fe12-49c5-bc52-807cb484d709__search_files,mcp__08ee302d-fe12-49c5-bc52-807cb484d709__create_file,mcp__08ee302d-fe12-49c5-bc52-807cb484d709__download_file_content,mcp__7de35e3f-bb0d-4345-9594-8d01194744b2__getConfluencePage,mcp__7de35e3f-bb0d-4345-9594-8d01194744b2__updateConfluencePage,mcp__cowork__present_files"
```

Server IDs rotate between installs. If a `select:` lookup misses, search by keyword
(`call_aws`, `drive create file`, `updateConfluencePage`) and use whichever AWS, Google
Drive and Atlassian MCP is connected.

`search_files` hits carry `id`, `title`, `mimeType`, `viewUrl`, `parentId`, `createdTime`
and `modifiedTime` — confirmed against the live folder — so `get_file_metadata` is not
needed. If a hit ever lacks `viewUrl`, load `get_file_metadata` rather than guessing a URL.

`call_aws` constraints: the command must start with `aws`; **no pipes, shell redirection,
command substitution, or env vars**. Use `--query` to slim responses down — but note it is
client-side JMESPath, applied after the CLI has already fetched and aggregated everything,
so it filters output rather than reducing work. Its file arguments are confined to its own
working directory (`/tmp/aws-api-mcp/workdir`).

---

## Phase 1 — Check AWS credentials (hard gate)

```
call_aws: aws sts get-caller-identity
```

The AWS MCP server drives the **AWS CLI installed on this machine** and inherits its SSO
session, so this reflects local credential state. If it fails with *"Your session has
expired or credentials have changed"*:

- **Stop.** Do not attempt `aws sso login`, a browser login, or any other route to
  credentials — it needs interactive approval an unattended run cannot give.
- Go straight to Phase 7 in **BLOCKED** mode: write the report, skip every state mutation.

Never continue past a failure here. The response contains the AWS account ID and user ARN —
do not copy either into the report.

## Phase 2 — Enumerate the buckets

One call per bucket:

```
call_aws: aws s3api list-objects-v2 --bucket <BUCKET> --output json --query "Contents[].[Key,Size,ETag]"
```

The AWS CLI paginates this operation automatically and returns the aggregated `Contents`,
so a single call normally gives every object. Two traps:

- `--query` is **client-side** JMESPath applied after that aggregation, so asking for
  `IsTruncated` typically yields `null`. **Never read a null or absent `IsTruncated` as
  proof that the listing is complete** — it says nothing either way.
- Consequently there is no reliable truncation signal here. Completeness is established in
  Phase 4 instead, by checking the corpus against `config-snapshot.json`, and enforced by
  the guard in Phase 6a. If a bucket returns a suspiciously round number of objects
  (exactly 1000), re-list it with `--page-size 1000 --max-items 100000` and compare counts
  before trusting either result.

Record the object count per bucket. An empty bucket is a finding for the report, not a
reason to abandon the other three.

## Phase 3 — Classify against the manifest

`manifest.json` maps `"<bucket>/<key>"` → `{ "etag", "size", "driveFileId", "syncedAt" }`.

- **new** — key absent from the manifest
- **changed** — ETag differs
- **unchanged** — ETag matches → no download, no upload
- **deleted** — in the manifest, gone from S3 → leave Drive alone (the Drive MCP cannot
  delete) and list under *orphaned in Drive*

Hold all of this **in memory**. Do not write `manifest.json` until Phase 7 — a mid-run
write would record files as synced that later failed to upload.

### Baseline runs (manifest empty or missing)

Do not mark everything new: the Drive folders already hold files from earlier runs of the
retired script, and blind re-upload would duplicate every one. Reconcile against Drive
once:

1. Page through the bucket folder with `search_files: "parentId = '<folder id>'"`,
   `pageSize=100`, following `pageToken`.
2. For a title that already exists, look at its `mimeType`:
   - **A Google-native type** (`application/vnd.google-apps.*`) means the retired script
     uploaded it without a conversion guard and the YAML was converted into a Doc. It is
     not recoverable by comparison. Upload a correct copy and list the converted one under
     *stale copies to prune*. Do not try to diff it.
   - Otherwise pull it with `download_file_content`, decode the base64, and compare to the
     S3 content. Equal → **unchanged**, record in the manifest without uploading.
3. Only genuinely new or differing files get uploaded.

This costs one download per pre-existing plain file, on the baseline run only.

## Phase 4 — Fetch content

Two jobs, and the second is easy to get wrong:

1. every **new + changed** object, so it can be uploaded to Drive
2. **every `default.yml`, without exception**, so Phase 6 has a complete corpus

> **The corpus must be complete.** Phase 6 rebuilds the Confluence table from scratch out
> of whatever `default.yml` files it finds. If the parse root holds only the files that
> changed this run, the ~74-row table gets replaced by a handful of rows and the rest are
> reported as removed countries. Before Phase 6, `.cache/configs/` must contain a
> `default.yml` for every country in `config-snapshot.json` **and** every country present
> in S3 — fetch any that are missing even when unchanged. Count them and confirm the number
> before moving on. `analyze_configs.py` has guards, but they are a backstop for mistakes,
> not a substitute for this check.

`.cache/configs/` persists between runs, so in the steady state only changed files are
fetched. If it has been cleared, refetch the whole corpus.

**Strategy A — bulk sync (try once, verify).** `call_aws` confines file arguments to its
own working directory, so an absolute `<repo>` destination is likely rejected, and a
relative one lands in `/tmp/aws-api-mcp/workdir` where neither `Read` nor the sandbox can
see it:

```
call_aws: aws s3 sync s3://<BUCKET>/ /Users/n.mahmood/Ai-directory/DH-Repos/s3-to-gdrive/.cache/configs/<BUCKET>/
```

Only treat this as successful if the files are actually readable at that path afterwards —
check with `Read` or `ls`. A zero-error response is not proof. Otherwise use Strategy B.

**Strategy B — per-file streaming (reliable fallback).** `-` is an ordinary CLI argument,
not a shell redirect, so it passes validation:

```
call_aws: aws s3 cp s3://<BUCKET>/<KEY> -
```

Write each result to `.cache/configs/<BUCKET>/<KEY>` with `Write`. Use this for the
changed + new set plus any missing `default.yml`, never the whole bucket.

**Binary objects.** Both strategies assume UTF-8; these buckets hold YAML. Skip anything
that is not valid UTF-8 and list it under *skipped (binary)* — streaming binary through a
tool response corrupts it. Do not guess an encoding.

Note in the report which strategy was used.

## Phase 5 — Upload to Google Drive

### 5a. Resolve the bucket folder (reuse, never duplicate)

```
Drive search_files: query "parentId = '1XLm6DJH-4-jgtv77w6mQyV6EYy9SjNVd' and title = '<BUCKET>'"
```

- One hit → use its `id`.
- Several → use the most recently created, flag the duplicates in the report.
- None → create it. Use `contentMimeType`; the schema marks the older `mimeType` field
  "DEPRECATED. DO NOT USE!!", so do not lead with it:
  ```
  Drive create_file: title=<BUCKET>,
                     contentMimeType="application/vnd.google-apps.folder",
                     parentId="1XLm6DJH-4-jgtv77w6mQyV6EYy9SjNVd"
  ```
  **Then confirm the returned object's `mimeType` is `application/vnd.google-apps.folder`.**
  If a plain file came back, retry once adding `mimeType="application/vnd.google-apps.folder"`
  as well. If that still yields a non-folder, stop and report — uploading into a non-folder
  parent scatters files into the wrong place.

### 5b. Upload new and changed files only

Titles keep the S3 key as a flat name including slashes (`de/default.yml`), matching what
is already in Drive.

```
Drive create_file: title="<key>", parentId="<bucket folder id>",
                   textContent="<contents>",
                   contentMimeType="text/yaml",
                   disableConversionToGoogleType=true
```

`disableConversionToGoogleType=true` is **required** — without it Drive converts the upload
into a Google Doc and the YAML becomes unusable. That is exactly how the legacy converted
files in Phase 3 came to exist. If `text/yaml` is rejected, retry with `text/plain`, flag
still set.

**No update, no delete.** A changed file leaves the previous copy in place under the same
title. Capture the superseded file's `id` and `viewUrl` from the Phase 3/5a
`search_files` results and list them under *stale copies to prune*. Never re-upload an
unchanged file — that is the only thing keeping duplicates in check.

Retry a failed upload up to 3 times with backoff. If it still fails, carry on and record
it; one bad file must not abort the run. Track exactly which uploads succeeded — Phase 7
records only those.

## Phase 6 — Refresh the Confluence table

Skip this entire phase if `.cache/configs/` holds no parseable `default.yml`.

### 6a. Analyse

Pure computation — run it, don't do it by hand:

Pass `--expect-countries` set to the number of entries in `config-snapshot.json` (74 as
seeded). It is the floor that still applies if the snapshot is ever lost:

```bash
pip install pyyaml --break-system-packages -q
python3 <repo>/scripts/analyze_configs.py \
  --root <repo>/.cache/configs \
  --snapshot <repo>/config-snapshot.json \
  --expect-countries <count from config-snapshot.json> \
  --out-json <repo>/.s3sync/summary.json \
  --out-html <repo>/.s3sync/table.html
```

Exit codes: `0` fine · `1` no configs found · `2` bad arguments or an unreadable snapshot ·
`3` too few countries — either the shrink threshold or `--expect-countries` tripped.

**On exit 1, 2 or 3, stop and report.** Do not work around it:

- **3** means the parse root is incomplete — go back to Phase 4 and fetch the missing
  `default.yml` files. Pass `--allow-shrink` only if you have positively confirmed in the
  S3 listing that those countries are gone; it disables the guard protecting the table.
- **2** on a snapshot read means the snapshot is corrupt. Do not delete it to get past the
  error — that disarms the shrink guard. Repair it, or restore it from git.

If the script reports *"no previous snapshot — treating this as a baseline run"* but the
Confluence page already contains a populated coverage table, **stop**. The snapshot has
gone missing and the guard is running blind.

Do not pass `--write-snapshot` yet; a failed push must not lose the diff.

### 6b. Splice and push

```
getConfluencePage: cloudId=89647aba-…, pageId=1730215943, contentFormat="html"
```

Save the fetched HTML to `<repo>/reports/backups/page-<YYYY-MM-DD>.html` **before**
pushing. That directory is committed and never auto-cleaned, so the revert path survives.

Locate the `<h2>` whose text is *Config Coverage per Country*. Match tolerantly on the
text — the element may carry attributes such as `data-local-id`, so a literal match on
`<h2>Config Coverage per Country</h2>` can miss. Then:

- **If there is exactly one match:** keep everything above and including that heading
  byte-for-byte — the platform summary table and notes above it are hand-maintained and
  must never be rewritten — and replace everything below it with the generated table.
  Before doing so, check what is actually below it: if it is anything other than the
  intro paragraph and a single coverage table (for example a new hand-written section
  someone added), stop and report rather than deleting it.
- **If there are zero matches:** append the heading plus the table. Overwrite nothing. Note
  in the report that the heading was not found — a case or whitespace variant would land
  here and silently create a *second* coverage section, so this outcome needs a human look.
- **If there are two or more:** stop and report. Do not guess which one to cut at;
  choosing wrong deletes real content.

**Row-count cross-check — do this before pushing.** The fetched page HTML is already in
hand, so count the `<tr>` rows in its existing coverage table and compare to the generated
one. If the new table has fewer rows, **stop and report**; do not push. This is the last
line of defence and it catches every path the other guards can miss — a lost snapshot, a
shrink that squeezed under the 10% threshold, malformed YAML that silently dropped a
country. A drop is only legitimate if the S3 listing positively confirms those countries
are gone.

```
updateConfluencePage: cloudId=…, pageId=1730215943, contentFormat="html",
                      body=<spliced html>,
                      versionMessage="Automated TES config sync — <YYYY-MM-DD>"
```

The generated table is deterministic — identical configs produce identical HTML. So if the
spliced body matches the current body exactly, **skip the push** and report a no-op rather
than bumping the page version for nothing. The sync date lives in `versionMessage`, which
is why the body carries no timestamp.

### 6c. Save the snapshot

Only after a successful push or a confirmed no-op:

```bash
python3 <repo>/scripts/analyze_configs.py --root <repo>/.cache/configs \
  --snapshot <repo>/config-snapshot.json --write-snapshot
```

Re-running with `--write-snapshot` alone is safe: the table HTML is deterministic, so the
artifacts already written in 6a still match what was pushed.

## Phase 7 — State and report

**On a BLOCKED run (Phase 1 failed): do steps 2 and 3 only** — write the report and present
it. Leave `manifest.json`, `config-snapshot.json` and `.cache/` untouched; overwriting the
manifest with nothing forces an unnecessary full baseline next week.

1. Write `manifest.json` from the in-memory state, recording **only** files that are
   confirmed present in Drive — unchanged ones carried forward, plus uploads that
   succeeded. A file whose upload failed must not appear as synced, or the next run will
   skip it forever.
2. Write `reports/sync-<YYYY-MM-DD>.md`:
   - status (success / partial / blocked), and which Phase 4 strategy was used
   - per bucket: objects listed, uploaded, skipped as unchanged, failed
   - config changes reported by `analyze_configs.py`
   - Confluence: updated (with version number) / no-op / skipped, and the backup path
   - *stale copies to prune*, *orphaned in Drive*, *skipped (binary)*, duplicate folders,
     unknown platforms, parse warnings

Two `analyze_configs.py` warnings deserve more than a mention in the report, because both
mean a country silently dropped out of the table:

- **`duplicate country_code`** — the same country appears in two buckets. The script keeps
  whichever it reaches first in sorted order, which is arbitrary (`production-ap-…` beats
  `production-eu-…`). Report which file won and flag it for a decision; do not assume the
  winner is authoritative.
- **`failed to parse`** — malformed YAML. That country vanishes from the table without
  tripping the shrink guard unless enough of them fail at once. Call it out explicitly.
3. Present the report with `present_files`.
4. Delete `.s3sync/`. **Keep `.cache/configs/`** — clearing it forces a full refetch next
   run. Keep `reports/backups/`. If the run failed, keep `.s3sync/` for diagnosis.

`manifest.json` and `config-snapshot.json` are the run-to-run state; mention in the report
that they changed so they can be committed. Do not run `git commit` unprompted.

## Guardrails

- Only ever write to Drive folder `1XLm6DJH-4-jgtv77w6mQyV6EYy9SjNVd` and Confluence page
  `1730215943`. Never create Confluence pages, never touch other Drive folders.
- Never print AWS credentials, SSO tokens, account IDs, ARNs or Atlassian tokens into the
  report or into chat.
- S3 is read-only. No `aws s3 rm`, `mv`, `rb`, or any mutating AWS call.
- Config YAML is operational data, not PII, but do not paste whole files into chat —
  reference them by path.
- Never widen `--allow-shrink`, `--shrink-threshold`, or the Confluence splice rules to get
  a run to finish. A blocked run that reports honestly is the correct outcome.
- On an unattended run, never wait for input. Missing credentials → report and exit.
