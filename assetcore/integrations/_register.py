"""integrations/_register.py — import every integration so its provider
`@register` side-effects run.

Registration only happens when a module is imported. Import this once at service
or CLI startup to make all tracker providers discoverable by name in the config
layer. Add maya/blender/unreal/substance here as they gain registrations.
"""
from assetcore.integrations import jira, shotgrid  # noqa: F401
