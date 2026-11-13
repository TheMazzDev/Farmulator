"""Reference data every new farm business starts with. Farms can edit or add to it."""

from typing import Any

# Gestation (or incubation) lengths are typical figures; each farm can adjust them.
DEFAULT_SPECIES: list[dict[str, Any]] = [
    {"code": "pig", "name": "Pig", "default_tracking": "individual", "gestation_days": 114},
    {"code": "cattle", "name": "Cattle", "default_tracking": "individual", "gestation_days": 283},
    {"code": "goat", "name": "Goat", "default_tracking": "individual", "gestation_days": 150},
    {"code": "sheep", "name": "Sheep", "default_tracking": "individual", "gestation_days": 147},
    {"code": "chicken", "name": "Chicken", "default_tracking": "group", "gestation_days": 21},
]
