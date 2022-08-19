# Copyright 1999-2011 Gentoo Foundation
# Distributed under the terms of the GNU General Public License v2

from _emerge.Task import Task
from typing import Any


class Blocker(Task):

    __hash__ = Task.__hash__
    __slots__ = ("root", "atom", "cp", "eapi", "priority", "satisfied")

    def __init__(self, **kwargs: Any) -> None:
        Task.__init__(self, **kwargs)
        self.cp = self.atom.cp
        self._hash_key = ("blocks", self.root, self.atom, self.eapi)
        self._hash_value = hash(self._hash_key)
