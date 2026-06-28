# integrations — L4 (the disposable tool translators)

The only place a tool name appears. Each module is a thin translator from one tool's
vocabulary to the universal verbs, via the SDK's adapter bases. They import **only
the SDK** (firewall-enforced), and every tool/COM/CLI import is lazy so the modules
load cleanly headless. Adding one is a contract-tested weekend job — see the
[Development Guide](../DEVELOPMENT.md#7-extending-add-a-new-dcc-the-weekend-adapter).

## DCC adapters

### Maya

::: assetcore.integrations.maya

### 3ds Max

::: assetcore.integrations.max

### Blender

::: assetcore.integrations.blender

### Substance

::: assetcore.integrations.substance

### Photoshop (Concept Art)

::: assetcore.integrations.photoshop

## Engine adapters

### Unreal

::: assetcore.integrations.unreal

## Tracker adapters

### ShotGrid

::: assetcore.integrations.shotgrid

### Jira

::: assetcore.integrations.jira
