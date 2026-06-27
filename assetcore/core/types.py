"""Enums mirroring the schema's CHECK constraints exactly.

String-valued so they serialize trivially later (an enum member *is* its wire
string), and so equality against a plain string holds: RelType.DEPENDS_ON ==
"DEPENDS_ON".
"""
from enum import Enum


class Lifecycle(str, Enum):
    PROVISIONAL = "provisional"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


class RelType(str, Enum):
    INSTANCE_OF = "INSTANCE_OF"
    DERIVED_FROM = "DERIVED_FROM"
    VARIANT_OF = "VARIANT_OF"
    COMPOSED_OF = "COMPOSED_OF"
    DEPENDS_ON = "DEPENDS_ON"


class BindingMode(str, Enum):
    FLOAT = "float"
    PIN = "pin"
