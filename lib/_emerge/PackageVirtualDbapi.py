# Copyright 1999-2020 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

from typing import Any, Dict, List, Optional, Tuple, Union

from _emerge.Package import Package

from portage.dbapi import dbapi
from portage.dbapi.dep_expand import dep_expand
from portage.dep import Atom
from portage.package.ebuild.config import config
from portage.versions import _pkg_str


class PackageVirtualDbapi(dbapi):
    """
    A dbapi-like interface class that represents the state of the installed
    package database as new packages are installed, replacing any packages
    that previously existed in the same slot. The main difference between
    this class and fakedbapi is that this one uses Package instances
    internally (passed in via cpv_inject() and cpv_remove() calls).
    """

    def __init__(self, settings: config) -> None:
        dbapi.__init__(self)
        self.settings = settings
        self._match_cache = {}
        self._cp_map = {}
        self._cpv_map = {}

    def clear(self):
        """
        Remove all packages.
        """
        if self._cpv_map:
            self._clear_cache()
            self._cp_map.clear()
            self._cpv_map.clear()

    def copy(self):
        obj = PackageVirtualDbapi(self.settings)
        obj._match_cache = self._match_cache.copy()
        obj._cp_map = self._cp_map.copy()
        for k, v in obj._cp_map.items():
            obj._cp_map[k] = v[:]
        obj._cpv_map = self._cpv_map.copy()
        return obj

    def __bool__(self) -> bool:
        return bool(self._cpv_map)

    def __iter__(self) -> dict_valueiterator:
        return iter(self._cpv_map.values())

    def __contains__(self, item):
        existing = self._cpv_map.get(item.cpv)
        if existing is not None and existing == item:
            return True
        return False

    def get(
        self, item: Tuple[str, str, str, str, str], default: Optional[Any] = None
    ) -> Optional[Any]:
        cpv = getattr(item, "cpv", None)
        if cpv is None:
            if len(item) != 5:
                return default
            type_name, root, cpv, operation, repo_key = item

        existing = self._cpv_map.get(cpv)
        if existing is not None and existing == item:
            return existing
        return default

    def match_pkgs(self, atom: Atom) -> List[Package]:
        return [self._cpv_map[cpv] for cpv in self.match(atom)]

    def _clear_cache(self) -> None:
        if self._categories is not None:
            self._categories = None
        if self._match_cache:
            self._match_cache = {}

    def match(self, origdep: Atom, use_cache: int = 1) -> List[_pkg_str]:
        atom = dep_expand(origdep, mydb=self, settings=self.settings)
        cache_key = (atom, atom.unevaluated_atom)
        result = self._match_cache.get(cache_key)
        if result is not None:
            return result[:]
        result = list(self._iter_match(atom, self.cp_list(atom.cp)))
        self._match_cache[cache_key] = result
        return result[:]

    def cpv_exists(self, cpv: _pkg_str, myrepo: Optional[Any] = None) -> bool:
        return cpv in self._cpv_map

    def cp_list(self, mycp: str, use_cache: int = 1) -> List[_pkg_str]:
        # NOTE: Cache can be safely shared with the match cache, since the
        # match cache uses the result from dep_expand for the cache_key.
        cache_key = (mycp, mycp)
        cachelist = self._match_cache.get(cache_key)
        if cachelist is not None:
            return cachelist[:]
        cpv_list = self._cp_map.get(mycp)
        if cpv_list is None:
            cpv_list = []
        else:
            cpv_list = [pkg.cpv for pkg in cpv_list]
        self._cpv_sort_ascending(cpv_list)
        self._match_cache[cache_key] = cpv_list
        return cpv_list[:]

    def cp_all(self, sort=False):
        return sorted(self._cp_map) if sort else list(self._cp_map)

    def cpv_all(self):
        return list(self._cpv_map)

    def cpv_inject(self, pkg: Package) -> None:
        cp_list = self._cp_map.get(pkg.cp)
        if cp_list is None:
            cp_list = []
            self._cp_map[pkg.cp] = cp_list
        e_pkg = self._cpv_map.get(pkg.cpv)
        if e_pkg is not None:
            if e_pkg == pkg:
                return
            self.cpv_remove(e_pkg)
        for e_pkg in cp_list:
            if e_pkg.slot_atom == pkg.slot_atom:
                if e_pkg == pkg:
                    return
                self.cpv_remove(e_pkg)
                break
        cp_list.append(pkg)
        self._cpv_map[pkg.cpv] = pkg
        self._clear_cache()

    def cpv_remove(self, pkg: Package) -> None:
        old_pkg = self._cpv_map.get(pkg.cpv)
        if old_pkg != pkg:
            raise KeyError(pkg)
        self._cp_map[pkg.cp].remove(pkg)
        del self._cpv_map[pkg.cpv]
        self._clear_cache()

    def aux_get(
        self,
        cpv: Union[_pkg_str, str],
        wants: Union[List[str], Tuple[str, ...]],
        myrepo: Optional[Any] = None,
    ) -> List[str]:
        metadata = self._cpv_map[cpv]._metadata
        return [metadata.get(x, "") for x in wants]

    def aux_update(self, cpv: _pkg_str, values: Dict[str, str]) -> None:
        self._cpv_map[cpv]._metadata.update(values)
        self._clear_cache()
