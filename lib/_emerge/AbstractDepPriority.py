# Copyright 1999-2013 Gentoo Foundation
# Distributed under the terms of the GNU General Public License v2

import copy
from portage.util.SlotObject import SlotObject
from _emerge.DepPriority import DepPriority


class AbstractDepPriority(SlotObject):
    __slots__ = (
        "buildtime",
        "buildtime_slot_op",
        "runtime",
        "runtime_post",
        "runtime_slot_op",
    )

    def __lt__(self, other: DepPriority) -> bool:
        return self.__int__() < other

    def __le__(self, other):
        return self.__int__() <= other

    def __eq__(self, other):
        return self.__int__() == other

    def __ne__(self, other):
        return self.__int__() != other

    def __gt__(self, other: int) -> bool:
        return self.__int__() > other

    def __ge__(self, other):
        return self.__int__() >= other

    def copy(self) -> DepPriority:
        return copy.copy(self)
