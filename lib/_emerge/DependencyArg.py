from _emerge.RootConfig import RootConfig
from _emerge.Package import Package
from _emerge.SetArg import SetArg
from typing import Union

# Copyright 1999-2020 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2


class DependencyArg:

    __slots__ = ("arg", "force_reinstall", "internal", "reset_depth", "root_config")

    def __init__(
        self,
        arg: str = None,
        force_reinstall: bool = False,
        internal: bool = False,
        reset_depth: bool = True,
        root_config: RootConfig = None,
    ) -> None:
        """
        Use reset_depth=False for special arguments that should not interact
        with depth calculations (see the emerge --deep=DEPTH option).
        """
        self.arg = arg
        self.force_reinstall = force_reinstall
        self.internal = internal
        self.reset_depth = reset_depth
        self.root_config = root_config

    def __eq__(self, other: Union[Package, SetArg]) -> bool:
        if self.__class__ is not other.__class__:
            return False
        return self.arg == other.arg and self.root_config.root == other.root_config.root

    def __hash__(self) -> int:
        return hash((self.arg, self.root_config.root))

    def __str__(self):
        return "%s" % (self.arg,)
