"""Imports every module's models so the metadata is complete (used by Alembic)."""

from farmulator.core.models import Base
from farmulator.modules.farm import models as farm_models
from farmulator.modules.livestock import models as livestock_models
from farmulator.modules.stock import models as stock_models

__all__ = ["Base", "farm_models", "livestock_models", "stock_models"]

# Tables whose rows belong to one tenant. Each gets a row-level security policy.
TENANT_TABLES = [
    table.name for table in Base.metadata.sorted_tables if "tenant_id" in table.columns
]

# Ledgers the app may read and append to, but never change or delete from.
APPEND_ONLY_TABLES = ["animal_event", "stock_movement"]
