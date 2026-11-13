"""The inbreeding maths, checked against textbook values."""

import uuid
from fractions import Fraction

import pytest

from farmulator.modules.livestock.pedigree import PedigreeCycleError, RelationshipCalculator


def ids(*names: str) -> dict[str, uuid.UUID]:
    return {n: uuid.uuid4() for n in names}


def test_unrelated_parents_give_zero() -> None:
    a = ids("sire", "dam")
    calc = RelationshipCalculator({a["sire"]: (None, None), a["dam"]: (None, None)})
    assert calc.offspring_inbreeding(a["sire"], a["dam"]) == 0


def test_full_sibling_mating_is_one_quarter() -> None:
    a = ids("gs", "gd", "brother", "sister")
    calc = RelationshipCalculator(
        {
            a["gs"]: (None, None),
            a["gd"]: (None, None),
            a["brother"]: (a["gs"], a["gd"]),
            a["sister"]: (a["gs"], a["gd"]),
        }
    )
    assert calc.relationship(a["brother"], a["sister"]) == Fraction(1, 2)
    assert calc.offspring_inbreeding(a["brother"], a["sister"]) == Fraction(1, 4)


def test_half_sibling_mating_is_one_eighth() -> None:
    a = ids("boar", "sow1", "sow2", "son", "daughter")
    calc = RelationshipCalculator(
        {
            a["boar"]: (None, None),
            a["sow1"]: (None, None),
            a["sow2"]: (None, None),
            a["son"]: (a["boar"], a["sow1"]),
            a["daughter"]: (a["boar"], a["sow2"]),
        }
    )
    assert calc.offspring_inbreeding(a["son"], a["daughter"]) == Fraction(1, 8)


def test_sire_daughter_mating_is_one_quarter() -> None:
    a = ids("boar", "sow", "daughter")
    calc = RelationshipCalculator(
        {a["boar"]: (None, None), a["sow"]: (None, None), a["daughter"]: (a["boar"], a["sow"])}
    )
    assert calc.relationship(a["boar"], a["daughter"]) == Fraction(1, 2)
    assert calc.offspring_inbreeding(a["boar"], a["daughter"]) == Fraction(1, 4)


def test_inbred_animal_relationship_with_itself_includes_its_inbreeding() -> None:
    a = ids("gs", "gd", "brother", "sister", "inbred")
    calc = RelationshipCalculator(
        {
            a["gs"]: (None, None),
            a["gd"]: (None, None),
            a["brother"]: (a["gs"], a["gd"]),
            a["sister"]: (a["gs"], a["gd"]),
            a["inbred"]: (a["brother"], a["sister"]),
        }
    )
    assert calc.inbreeding(a["inbred"]) == Fraction(1, 4)
    assert calc.relationship(a["inbred"], a["inbred"]) == Fraction(5, 4)


def test_parents_outside_the_loaded_pedigree_count_as_unknown() -> None:
    a = ids("sire", "dam")
    missing = uuid.uuid4()
    calc = RelationshipCalculator({a["sire"]: (missing, None), a["dam"]: (missing, None)})
    assert calc.offspring_inbreeding(a["sire"], a["dam"]) == 0


def test_common_ancestors_include_a_parent_that_is_the_other_parents_ancestor() -> None:
    a = ids("boar", "sow", "daughter")
    calc = RelationshipCalculator(
        {a["boar"]: (None, None), a["sow"]: (None, None), a["daughter"]: (a["boar"], a["sow"])}
    )
    assert calc.common_ancestors(a["boar"], a["daughter"]) == {a["boar"]}


def test_cycle_is_rejected() -> None:
    a = ids("x", "y")
    with pytest.raises(PedigreeCycleError):
        RelationshipCalculator({a["x"]: (a["y"], None), a["y"]: (a["x"], None)})
