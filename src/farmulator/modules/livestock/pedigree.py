"""Pedigree maths: additive relationship and inbreeding coefficients.

Uses the recursive (tabular) method: animals are ordered so parents come before
offspring, then for i older than j:

    a(i, i) = 1 + F(i)
    a(i, j) = ( a(i, sire(j)) + a(i, dam(j)) ) / 2
    F(x)    = a(sire(x), dam(x)) / 2

Unknown parents, and ancestors beyond the depth loaded, count as unrelated founders.
Exact fractions avoid rounding drift; callers round for display.
"""

import uuid
from collections.abc import Mapping
from fractions import Fraction
from functools import lru_cache

Parents = tuple[uuid.UUID | None, uuid.UUID | None]


class PedigreeCycleError(ValueError):
    """The data says an animal is its own ancestor."""


class RelationshipCalculator:
    def __init__(self, parents: Mapping[uuid.UUID, Parents]) -> None:
        known = set(parents)
        # Parents outside the loaded set are treated as unknown founders.
        self._parents: dict[uuid.UUID, Parents] = {
            animal: (sire if sire in known else None, dam if dam in known else None)
            for animal, (sire, dam) in parents.items()
        }
        self._order = self._topological_order()
        self._relationship = lru_cache(maxsize=None)(self._relationship_uncached)

    def _topological_order(self) -> dict[uuid.UUID, int]:
        order: dict[uuid.UUID, int] = {}
        visiting: set[uuid.UUID] = set()
        for start in self._parents:
            if start in order:
                continue
            # Iterative depth-first walk; an animal gets its index after both parents.
            stack: list[tuple[uuid.UUID, bool]] = [(start, False)]
            while stack:
                node, parents_done = stack.pop()
                if node in order:
                    continue
                if parents_done:
                    visiting.discard(node)
                    order[node] = len(order)
                    continue
                if node in visiting:
                    raise PedigreeCycleError(f"Animal {node} appears in its own ancestry.")
                visiting.add(node)
                stack.append((node, True))
                for parent in self._parents[node]:
                    if parent is not None and parent not in order:
                        if parent in visiting:
                            raise PedigreeCycleError(f"Animal {parent} is its own ancestor.")
                        stack.append((parent, False))
        return order

    def _relationship_uncached(self, i: uuid.UUID | None, j: uuid.UUID | None) -> Fraction:
        if i is None or j is None:
            return Fraction(0)
        if i == j:
            return 1 + self.inbreeding(i)
        if self._order[i] > self._order[j]:
            i, j = j, i  # expand the younger animal, which cannot be the other's ancestor
        sire, dam = self._parents[j]
        return (self._relationship(i, sire) + self._relationship(i, dam)) / 2

    def relationship(self, i: uuid.UUID, j: uuid.UUID) -> Fraction:
        """Additive genetic relationship between two animals (1 for an animal with itself
        when not inbred; 0.5 for parent and offspring or full siblings)."""
        return self._relationship(i, j)

    def inbreeding(self, animal: uuid.UUID) -> Fraction:
        sire, dam = self._parents[animal]
        if sire is None or dam is None:
            return Fraction(0)
        return self._relationship(sire, dam) / 2

    def offspring_inbreeding(self, sire: uuid.UUID, dam: uuid.UUID) -> Fraction:
        """Inbreeding coefficient of a calf, piglet or kid from mating these two."""
        return self._relationship(sire, dam) / 2

    def ancestors(self, animal: uuid.UUID) -> set[uuid.UUID]:
        found: set[uuid.UUID] = set()
        stack = [p for p in self._parents[animal] if p is not None]
        while stack:
            node = stack.pop()
            if node not in found:
                found.add(node)
                stack.extend(p for p in self._parents[node] if p is not None)
        return found

    def common_ancestors(self, a: uuid.UUID, b: uuid.UUID) -> set[uuid.UUID]:
        """Shared ancestors, counting each animal as part of its own line."""
        return (self.ancestors(a) | {a}) & (self.ancestors(b) | {b})
