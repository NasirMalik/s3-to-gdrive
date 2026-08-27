#!/usr/bin/env python3
"""
analyze_configs.py — pure-computation helper for the TES config sync.

Does no network I/O and touches no credentials. All S3 / Drive / Confluence
access happens through MCP tools in the calling skill; this script only:

  1. parses the `default.yml` files already downloaded to a local directory
  2. emits a normalised JSON summary (one record per country)
  3. diffs that summary against the previous run's snapshot
  4. renders the "Config Coverage per Country" HTML table for Confluence

Usage:
  analyze_configs.py --root DIR [--snapshot FILE] [--out-json FILE]
                     [--out-html FILE] [--write-snapshot]

  --root            directory containing the synced bucket trees
  --snapshot        previous snapshot JSON (default: config-snapshot.json)
  --out-json        write the new summary here
  --out-html        write the Confluence table HTML here
  --write-snapshot  overwrite the snapshot with the new summary

Exit codes: 0 success, 1 no configs found, 2 bad arguments,
            3 country set shrank past the safety threshold (see --allow-shrink).
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("[error] pyyaml is required: pip install pyyaml --break-system-packages")

RANGE_FORMAT = "DISPLAY_FORMAT_MINUTE_RANGE"

# Display labels as they already appear on the Confluence page. Keep these in
# sync with the page so a re-render does not produce a spurious diff.
PLATFORM_DISPLAY = {
    "pandora": "Pandora/Foodora",
    "peya": "PeYa",
    "hungerstation": "HungerStation",
    "talabat": "Talabat",
    "efood": "Efood/Foody",
    "glovo": "Glovo",
    "woowa": "Woowa",
}

TRACKED_FIELDS = [
    "platform",
    "pickup_is_range",
    "delivery_is_range",
    "has_standard",
    "has_priority",
    "has_saver",
    "has_capping",
    "rounding_strategy",
]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def analyze_config(data: dict) -> dict:
    """Extract the tracked properties from one parsed default.yml document."""
    display_formats: dict[str, str] = {}  # delivery_mode -> format string
    ranges: set[str] = set()              # delivery options with range bounds
    has_capping = False
    rounding_strategy = None

    for section in data.get("pdt") or []:
        if not isinstance(section, dict):
            continue
        if "display_format" in section:
            for fmt in section.get("display_format") or []:
                mode = (fmt.get("conditions") or {}).get("delivery_mode", "ALL")
                display_formats[mode] = fmt.get("format", "")
        elif "ranges" in section:
            for entry in section.get("ranges") or []:
                ranges.add(entry.get("delivery_option", "STANDARD"))
        elif "capping" in section:
            if section.get("capping"):
                has_capping = True
        elif "rounding" in section:
            for entry in section.get("rounding") or []:
                rounding_strategy = entry.get("strategy")

    # A mode-specific format wins; otherwise fall back to the unconditional one.
    pickup_fmt = display_formats.get("PICKUP", display_formats.get("ALL"))
    delivery_fmt = display_formats.get("DELIVERY", display_formats.get("ALL"))

    cc_raw = data.get("country_code", "?")
    if isinstance(cc_raw, list):
        cc_raw = ",".join(str(x) for x in cc_raw)

    return {
        "country_code": cc_raw,
        "platform": data.get("platform", "unknown"),
        "pickup_is_range": pickup_fmt == RANGE_FORMAT,
        "delivery_is_range": delivery_fmt == RANGE_FORMAT,
        "has_standard": "STANDARD" in ranges,
        "has_priority": "PRIORITY" in ranges,
        "has_saver": "SAVER" in ranges,
        "has_capping": has_capping,
        "rounding_strategy": rounding_strategy or "—",
    }


def collect_configs(root: Path) -> tuple[list[dict], list[str]]:
    """Parse every default.yml under root. Returns (configs, warnings)."""
    configs: list[dict] = []
    warnings: list[str] = []
    seen: dict[str, Path] = {}

    for path in sorted(root.rglob("default.yml")):
        try:
            data = yaml.safe_load(path.read_text())
        except Exception as exc:  # malformed YAML must not abort the run
            warnings.append(f"failed to parse {path}: {exc}")
            continue
        if not isinstance(data, dict) or not data:
            warnings.append(f"skipped {path}: empty or not a mapping")
            continue

        record = analyze_config(data)
        cc = record["country_code"]
        if cc in seen:
            warnings.append(
                f"duplicate country_code {cc!r}: {path} also seen at {seen[cc]} — keeping the first"
            )
            continue
        if record["platform"] not in PLATFORM_DISPLAY:
            warnings.append(
                f"unknown platform {record['platform']!r} for country {cc!r} ({path}): "
                "no display label mapped, the raw value will be rendered — add it to PLATFORM_DISPLAY"
            )

        seen[cc] = path
        record["_source"] = str(path.relative_to(root))
        configs.append(record)

    return configs, warnings


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------


def detect_changes(old: dict, configs: list[dict]) -> list[str]:
    """Human-readable diff between the previous snapshot and the new configs."""
    new = {c["country_code"]: c for c in configs}
    changes: list[str] = []

    for cc in sorted(set(new) - set(old)):
        changes.append(f"+ {cc}: new country added (platform={new[cc]['platform']})")
    for cc in sorted(set(old) - set(new)):
        changes.append(f"- {cc}: country removed")
    for cc in sorted(set(old) & set(new)):
        for field in TRACKED_FIELDS:
            before, after = old[cc].get(field), new[cc].get(field)
            if before != after:
                changes.append(f"~ {cc}.{field}: {before!r} → {after!r}")

    return changes


# ---------------------------------------------------------------------------
# Confluence table
# ---------------------------------------------------------------------------


def _ck(value: bool) -> str:
    return "✓" if value else "✗"


def build_country_table(configs: list[dict]) -> str:
    """Render the auto-generated section body as Confluence-compatible HTML.

    Deterministic on purpose: identical configs produce byte-identical HTML, so the
    caller can skip pushing an unchanged page. The sync time goes in the Confluence
    version message instead of the body — embedding a timestamp here would make every
    weekly run look like a change and bump the page version for nothing.
    """
    columns = [
        "country_id",
        "Platform",
        "Pickup = Range",
        "Delivery = Range",
        "OD Standard Range",
        "OD Priority Range",
        "OD Saver Range",
        "Capping",
        "Rounding Strategy",
    ]
    header = "<thead><tr>" + "".join(f"<th><p>{c}</p></th>" for c in columns) + "</tr></thead>"

    rows = []
    for c in sorted(configs, key=lambda x: x["country_code"]):
        platform = PLATFORM_DISPLAY.get(c["platform"], c["platform"])
        cells = [
            f"<code>{html.escape(str(c['country_code']))}</code>",
            html.escape(platform),
            _ck(c["pickup_is_range"]),
            _ck(c["delivery_is_range"]),
            _ck(c["has_standard"]),
            _ck(c["has_priority"]),
            _ck(c["has_saver"]),
            _ck(c["has_capping"]),
            html.escape(str(c["rounding_strategy"])),
        ]
        rows.append("<tr>" + "".join(f"<td><p>{v}</p></td>" for v in cells) + "</tr>")

    return (
        "<p>Derived directly from <code>default.yml</code> files in the TES-P production "
        "config folder. Each row represents one country. Columns reflect the presence (✓) "
        "or absence (✗) of each config feature.</p>"
        f'<table data-layout="wide">{header}<tbody>{"".join(rows)}</tbody></table>'
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", required=True, type=Path, help="directory of synced bucket trees")
    ap.add_argument("--snapshot", type=Path, default=Path("config-snapshot.json"))
    ap.add_argument("--out-json", type=Path)
    ap.add_argument("--out-html", type=Path)
    ap.add_argument("--write-snapshot", action="store_true")
    ap.add_argument(
        "--allow-shrink",
        action="store_true",
        help="permit the country set to shrink by more than the safety threshold "
        "(only pass this when countries were genuinely decommissioned)",
    )
    ap.add_argument(
        "--shrink-threshold",
        type=float,
        default=0.10,
        help="maximum fraction of countries that may disappear before the run aborts (default 0.10)",
    )
    ap.add_argument(
        "--expect-countries",
        type=int,
        help="abort unless at least this many countries were parsed. Use it when there is no "
        "snapshot to compare against, so an incomplete parse root still cannot slip through.",
    )
    args = ap.parse_args()

    if not args.root.is_dir():
        print(f"[error] --root {args.root} is not a directory", file=sys.stderr)
        return 2

    configs, warnings = collect_configs(args.root)
    for w in warnings:
        print(f"[warn] {w}", file=sys.stderr)

    if not configs:
        print(f"[error] no default.yml files found under {args.root}", file=sys.stderr)
        return 1

    old = {}
    if args.snapshot.exists():
        # A snapshot that exists but cannot be read must NOT degrade to "baseline run" —
        # that would silently disarm the shrink guard below, which is the only thing
        # standing between an incomplete parse root and a truncated Confluence table.
        try:
            old = json.loads(args.snapshot.read_text())
        except Exception as exc:
            print(
                f"[error] snapshot {args.snapshot} exists but could not be parsed: {exc}\n"
                "[error] Refusing to continue: treating this as a baseline run would disable "
                "the shrink guard. Repair or delete the snapshot deliberately.",
                file=sys.stderr,
            )
            return 2
        if not isinstance(old, dict):
            print(f"[error] snapshot {args.snapshot} is not a JSON object", file=sys.stderr)
            return 2

    changes = detect_changes(old, configs)

    print(f"[info] parsed {len(configs)} country config(s) from {args.root}")

    # Absolute floor, independent of the snapshot. Covers the case the snapshot cannot:
    # a missing snapshot plus an incomplete parse root.
    if args.expect_countries is not None and len(configs) < args.expect_countries:
        print(
            f"[error] parsed {len(configs)} countries but expected at least "
            f"{args.expect_countries} — the parse root under {args.root} is incomplete.",
            file=sys.stderr,
        )
        return 3

    # Safety gate. The Confluence table is rebuilt wholesale from `configs`, so an
    # incomplete parse root would silently replace the full country table with a
    # partial one. Refuse to emit anything unless the shrink is explicitly approved.
    if old:
        lost = len(set(old) - {c["country_code"] for c in configs})
        allowed = int(len(old) * args.shrink_threshold)
        if lost > allowed and not args.allow_shrink:
            print(
                f"[error] {lost} of {len(old)} countries from the snapshot are missing from "
                f"{args.root} (threshold: {allowed}).\n"
                "[error] This usually means the parse root is incomplete — the Confluence table "
                "is rebuilt from scratch, so continuing would truncate it.\n"
                "[error] Verify every default.yml is present, or pass --allow-shrink if the "
                "countries were genuinely removed.",
                file=sys.stderr,
            )
            return 3
    if not old:
        print("[info] no previous snapshot — treating this as a baseline run")
    elif changes:
        print(f"[info] {len(changes)} config change(s) since last sync:")
        for line in changes:
            print(f"  {line}")
    else:
        print("[info] no config changes detected since last sync")

    def _write(path: Path, text: str, label: str) -> None:
        # The scratch directory is deleted at the end of every run, so recreate any
        # missing parent rather than dying with FileNotFoundError — which the caller
        # would otherwise misread as exit code 1, "no configs found".
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        print(f"[info] wrote {label} to {path}")

    if args.out_json:
        _write(
            args.out_json,
            json.dumps({"configs": configs, "changes": changes, "warnings": warnings}, indent=2, ensure_ascii=False),
            "summary",
        )

    if args.out_html:
        _write(args.out_html, build_country_table(configs), "Confluence table")

    if args.write_snapshot:
        snapshot = {c["country_code"]: {k: c[k] for k in TRACKED_FIELDS} for c in configs}
        _write(args.snapshot, json.dumps(snapshot, indent=2, ensure_ascii=False), "snapshot")

    return 0


if __name__ == "__main__":
    sys.exit(main())
