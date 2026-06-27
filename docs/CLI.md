# `assetcore` CLI

The agnostic command-line interface — one subcommand per universal verb, over the
same `AssetcoreClient` (HTTP) every integration uses. Artists, production,
developers, and pipeline automation all drive the system through this one surface.
Pure stdlib `argparse` + the SDK client; it imports nothing below the HTTP boundary
(the SDK firewall covers it).

## Config
- `--url` / `$ASSETCORE_URL`   — service base url (default `http://127.0.0.1:8000`)
- `--token` / `$ASSETCORE_TOKEN` — auth token → authority (default `artist-token`)
- `--json` — print the raw service JSON (for scripting) instead of human text

Run as the installed console script `assetcore …`, or `python -m assetcore.sdk.cli …`.

## Commands
| Command | Authority | Purpose |
|---|---|---|
| `resolve <id>` | open | the three facets of an asset |
| `declare --type T --by U` | artist/engine | mint a provisional asset (prints the id) |
| `claim <id> --name --taxonomy --actor` | production | give a provisional asset identity |
| `rename <id> --name --actor [--taxonomy]` | production | relabel the identity facet only |
| `bind-source <id> <uri> --tool --rev --by` | artist | publish the source facet |
| `bind-runtime <id> <uri> --build` | engine/build | report the runtime facet |
| `relate <from> <to> <rel_type> [--mode float\|pin] [--pin N] [--actor]` | any | assert a typed edge |
| `relocate <id> <uri> --actor [--facet source\|runtime] [--rev]` | any | move the bytes (same identity/version) |
| `deprecate <id> --actor` | production | retire an identity |
| `impact <id>` / `dependents <id> [--rel-types a,b] [--depth N]` | open | what breaks if I touch this |
| `dependencies <id> [...]` | open | what this is built from |
| `used-by <id>` · `lineage <id>` | open | one-hop consumers / provenance |
| `stale-derivations <id>` | open | DERIVED_FROM edges whose source advanced |
| `floating <id>` | open | DEPENDS_ON edges still floating (pin before ship) |
| `find-similar <name> [--type]` | open | reuse-over-rebuild nudge (advisory) |
| `worklist` | open | provisional backfill queue (oldest first) |
| `move <id> --actor [--name][--taxonomy][--source][--source-rev][--runtime] [--yes]` | production | **rename + relocate in one op**, with an impact preview; omit `--yes` for preview only |
| `relocate-prefix <old> <new> --ids a,b,c --actor [--yes]` | any | **directory move**: remap a path prefix across many assets; omit `--yes` for preview |

`move` and `relocate-prefix` are the production rename/relocate tool: they print the
impact (transitive dependents) first and only mutate when you pass `--yes` — safe by
default. They wrap `assetcore.sdk.tools` (`impact_report`, `rename_relocate`,
`relocate_prefix`), which a GUI could reuse.

## Examples
```bash
export ASSETCORE_URL=http://127.0.0.1:8000
PROP=$(ASSETCORE_TOKEN=artist-token assetcore declare --type prop --by amy)
ASSETCORE_TOKEN=prod-token assetcore claim "$PROP" --name "Barrel" --taxonomy props/x --actor pat
ASSETCORE_TOKEN=prod-token assetcore relocate "$PROP" //depot/new/barrel.ma --actor pat --rev 42
assetcore impact "$PROP" --rel-types COMPOSED_OF,DEPENDS_ON --depth 3
assetcore resolve "$PROP"
```
Every command exits non-zero with `error: …` on failure (e.g. a 4xx from the service).
