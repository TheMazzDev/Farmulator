"""The example sow protocol every new farm business starts with.

It is an EXAMPLE, not veterinary advice: it is marked "not vet-approved", names no
products or doses, and each farm should set its own steps with its vet. Timings are
counted in days from the anchor (negative means before it). Pig gestation is taken as
114 days, so day 112 to 117 is the farrowing window.
"""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from farmulator.modules.breeding.models import (
    ProtocolStep,
    ProtocolTemplate,
    StepAnchor,
    StepTarget,
)
from farmulator.modules.tasks.models import TaskAction

A, T = StepAnchor, StepTarget

SOW_STEPS: list[dict[str, Any]] = [
    {
        "code": "pregnancy-check",
        "name": "Pregnancy check (scan)",
        "action": TaskAction.PREGNANCY_CHECK,
        "anchor": A.SERVICE,
        "offset_days": 28,
        "window_days": 7,
        "target": T.DAM,
        "reminder_days_before": 2,
    },
    {
        "code": "pre-farrow-vaccine-1",
        "name": "Pre-farrowing vaccine, first dose",
        "action": TaskAction.VACCINATE,
        "anchor": A.EXPECTED_BIRTH,
        "offset_days": -35,
        "window_days": 3,
        "target": T.DAM,
        "reminder_days_before": 3,
        "instructions": "For example against piglet scours. Use the product your vet advises.",
    },
    {
        "code": "pre-farrow-vaccine-2",
        "name": "Pre-farrowing vaccine, second dose",
        "action": TaskAction.VACCINATE,
        "anchor": A.EXPECTED_BIRTH,
        "offset_days": -14,
        "window_days": 3,
        "target": T.DAM,
        "reminder_days_before": 3,
    },
    {
        "code": "deworm-sow",
        "name": "Deworm sow",
        "action": TaskAction.DEWORM,
        "anchor": A.EXPECTED_BIRTH,
        "offset_days": -14,
        "window_days": 3,
        "target": T.DAM,
        "reminder_days_before": 3,
    },
    {
        "code": "move-to-farrowing",
        "name": "Wash sow and move to farrowing pen",
        "action": TaskAction.MOVE,
        "anchor": A.EXPECTED_BIRTH,
        "offset_days": -7,
        "window_days": 2,
        "target": T.DAM,
        "reminder_days_before": 2,
        "instructions": "Clean and disinfect the pen first. Check heat lamp and creep area.",
    },
    {
        "code": "farrowing",
        "name": "Farrowing due: record the litter",
        "action": TaskAction.BIRTH,
        "anchor": A.EXPECTED_BIRTH,
        "offset_days": -2,
        "window_days": 5,
        "target": T.DAM,
        "reminder_days_before": 2,
    },
    {
        "code": "piglet-processing",
        "name": "Piglets: teeth, tails and ear notches",
        "action": TaskAction.OTHER,
        "anchor": A.BIRTH,
        "offset_days": 1,
        "window_days": 2,
        "target": T.LITTER,
        "reminder_days_before": 0,
    },
    {
        "code": "piglet-iron",
        "name": "Piglets: iron injection",
        "action": TaskAction.TREAT,
        "anchor": A.BIRTH,
        "offset_days": 2,
        "window_days": 2,
        "target": T.LITTER,
        "reminder_days_before": 1,
    },
    {
        "code": "piglet-castration",
        "name": "Piglets: castrate males",
        "action": TaskAction.OTHER,
        "anchor": A.BIRTH,
        "offset_days": 5,
        "window_days": 3,
        "target": T.LITTER,
        "reminder_days_before": 1,
    },
    {
        "code": "piglet-vaccine",
        "name": "Piglets: vaccinate",
        "action": TaskAction.VACCINATE,
        "anchor": A.BIRTH,
        "offset_days": 21,
        "window_days": 4,
        "target": T.LITTER,
        "reminder_days_before": 2,
        "instructions": "For example against mycoplasma and circovirus, as your vet advises.",
    },
    {
        "code": "weaning",
        "name": "Wean litter: record count and weight",
        "action": TaskAction.WEANING,
        "anchor": A.BIRTH,
        "offset_days": 28,
        "window_days": 7,
        "target": T.DAM,
        "reminder_days_before": 2,
    },
    {
        "code": "sow-heat-check",
        "name": "Check sow for heat and plan service",
        "action": TaskAction.CHECK,
        "anchor": A.WEANING,
        "offset_days": 4,
        "window_days": 3,
        "target": T.DAM,
        "reminder_days_before": 1,
    },
]


async def seed_default_protocols(
    session: AsyncSession, tenant_id: uuid.UUID, species_by_code: dict[str, uuid.UUID]
) -> None:
    pig = species_by_code.get("pig")
    if pig is None:
        return
    template = ProtocolTemplate(
        id=uuid.uuid4(),
        tenant_id=tenant_id,
        species_id=pig,
        name="Sow breeding cycle (example)",
        is_default=True,
        approved_by_vet=False,
        notes="An example to start from. Review every step, product and timing with your vet.",
    )
    session.add(template)
    await session.flush()  # the template row must exist before its steps
    for step in SOW_STEPS:
        session.add(ProtocolStep(tenant_id=tenant_id, template_id=template.id, **step))
    await session.flush()
