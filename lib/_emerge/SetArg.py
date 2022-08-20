# Copyright 1999-2012 Gentoo Foundation
# Distributed under the terms of the GNU General Public License v2

from typing import Any

from _emerge.DependencyArg import DependencyArg

from portage._sets import SETPREFIX


class SetArg(DependencyArg):

    __slots__ = ("name", "pset")

    def __init__(self, pset: Any = None, **kwargs: Any) -> None:
        DependencyArg.__init__(self, **kwargs)
        self.pset = pset
        self.name = self.arg[len(SETPREFIX) :]
