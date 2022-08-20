from __future__ import annotations
from typing import Any, Optional


import portage
portage.proxy.lazyimport.lazyimport(
    "portage.cache.mappings.MutableMapping"
)


class Atom(str):

    """
    For compatibility with existing atom string manipulation code, this
    class emulates most of the str methods that are useful with atoms.
    """

    # Distiguishes package atoms from other atom types
    package = True

    # Distiguishes soname atoms from other atom types
    soname = False

    class _blocker:
        __slots__ = ("overlap",)

        class _overlap:
            __slots__ = ("forbid",)

            def __init__(self, forbid: bool = False) -> None:
                self.forbid = forbid

        def __init__(self, forbid_overlap: bool = False) -> None:
            self.overlap = self._overlap(forbid=forbid_overlap)

    def __new__(cls, s, *args, **kwargs):
        return str.__new__(cls, s)

    def __init__(
        self,
        s: str,
        unevaluated_atom: Optional[Any] = None,
        allow_wildcard: bool = False,
        allow_repo: Optional[bool] = None,
        _use: Optional[Any] = None,
        eapi: Optional[str] = None,
        is_valid_flag: Optional[Any] = None,
        allow_build_id: Optional[bool] = None,
    ) -> None:
        if isinstance(s, Atom):
            # This is an efficiency assertion, to ensure that the Atom
            # constructor is not called redundantly.
            raise TypeError(_("Expected %s, got %s") % (str, type(s)))

        if not isinstance(s, str):
            # Avoid TypeError from str.__init__ with PyPy.
            s = _unicode_decode(s)

        str.__init__(s)

        eapi_attrs = _get_eapi_attrs(eapi)
        atom_re = _get_atom_re(eapi_attrs)

        self.__dict__["eapi"] = eapi
        if eapi is not None:
            # If allow_repo is not set, use default from eapi
            if allow_repo is None:
                allow_repo = eapi_attrs.repo_deps
        else:
            # These parameters have "smart" defaults that are only
            # applied when the caller does not explicitly pass in a
            # True or False value.
            if allow_repo is None:
                allow_repo = True
            if allow_build_id is None:
                allow_build_id = True

        blocker_prefix = ""
        if "!" == s[:1]:
            blocker = self._blocker(forbid_overlap=("!" == s[1:2]))
            if blocker.overlap.forbid:
                blocker_prefix = s[:2]
                s = s[2:]
            else:
                blocker_prefix = s[:1]
                s = s[1:]
        else:
            blocker = False
        self.__dict__["blocker"] = blocker
        m = atom_re.match(s)
        build_id = None
        extended_syntax = False
        extended_version = None
        if m is None:
            if allow_wildcard:
                atom_re = _get_atom_wildcard_re(eapi_attrs)
                m = atom_re.match(s)
                if m is None:
                    raise InvalidAtom(self)
                m_group = m.group
                if m_group("star") is not None:
                    op = "=*"
                    base = atom_re.groupindex["star"]
                    cp = m_group(base + 1)
                    cpv = m_group("star")[1:]
                    extended_version = m_group(base + 4)
                else:
                    op = None
                    cpv = cp = m_group("simple")
                    if m_group(atom_re.groupindex["simple"] + 3) is not None:
                        raise InvalidAtom(self)
                if cpv.find("**") != -1:
                    raise InvalidAtom(self)
                slot = m_group("slot")
                repo = m_group("repo")
                use_str = None
                extended_syntax = True
            else:
                raise InvalidAtom(self)
        elif m.group("op") is not None:
            m_group = m.group
            base = atom_re.groupindex["op"]
            op = m_group(base + 1)
            cpv = m_group(base + 2)
            cp = m_group(base + 3)
            slot = m_group(atom_re.groups - 2)
            repo = m_group(atom_re.groups - 1)
            use_str = m_group(atom_re.groups)
            version = m_group(base + 4)
            if version is not None:
                if allow_build_id:
                    cpv_build_id = cpv
                    cpv = cp
                    cp = cp[: -len(version)]
                    build_id = cpv_build_id[len(cpv) + 1 :]
                    if len(build_id) > 1 and build_id[:1] == "0":
                        # Leading zeros are not allowed.
                        raise InvalidAtom(self)
                    try:
                        build_id = int(build_id)
                    except ValueError:
                        raise InvalidAtom(self)
                else:
                    raise InvalidAtom(self)
        elif m.group("star") is not None:
            base = atom_re.groupindex["star"]
            op = "=*"
            m_group = m.group
            cpv = m_group(base + 1)
            cp = m_group(base + 2)
            slot = m_group(atom_re.groups - 2)
            repo = m_group(atom_re.groups - 1)
            use_str = m_group(atom_re.groups)
            if m_group(base + 3) is not None:
                raise InvalidAtom(self)
        elif m.group("simple") is not None:
            op = None
            m_group = m.group
            cpv = cp = m_group(atom_re.groupindex["simple"] + 1)
            slot = m_group(atom_re.groups - 2)
            repo = m_group(atom_re.groups - 1)
            use_str = m_group(atom_re.groups)
            if m_group(atom_re.groupindex["simple"] + 2) is not None:
                raise InvalidAtom(self)

        else:
            raise AssertionError(_("required group not found in atom: '%s'") % self)
        self.__dict__["cp"] = cp
        try:
            self.__dict__["cpv"] = _pkg_str(cpv)
            self.__dict__["version"] = self.cpv.version
        except InvalidData:
            # plain cp, wildcard, or something
            self.__dict__["cpv"] = cpv
            self.__dict__["version"] = extended_version
        self.__dict__["repo"] = repo
        if slot is None:
            self.__dict__["slot"] = None
            self.__dict__["sub_slot"] = None
            self.__dict__["slot_operator"] = None
        else:
            slot_re = _get_slot_dep_re(eapi_attrs)
            slot_match = slot_re.match(slot)
            if slot_match is None:
                raise InvalidAtom(self)
            if eapi_attrs.slot_operator:
                self.__dict__["slot"] = slot_match.group(1)
                sub_slot = slot_match.group(2)
                if sub_slot is not None:
                    sub_slot = sub_slot.lstrip("/")
                if sub_slot in ("*", "="):
                    self.__dict__["sub_slot"] = None
                    self.__dict__["slot_operator"] = sub_slot
                else:
                    slot_operator = None
                    if sub_slot is not None and sub_slot[-1:] == "=":
                        slot_operator = sub_slot[-1:]
                        sub_slot = sub_slot[:-1]
                    self.__dict__["sub_slot"] = sub_slot
                    self.__dict__["slot_operator"] = slot_operator
                if self.slot is not None and self.slot_operator == "*":
                    raise InvalidAtom(self)
            else:
                self.__dict__["slot"] = slot
                self.__dict__["sub_slot"] = None
                self.__dict__["slot_operator"] = None
        self.__dict__["operator"] = op
        self.__dict__["extended_syntax"] = extended_syntax
        self.__dict__["build_id"] = build_id

        if not (repo is None or allow_repo):
            raise InvalidAtom(self)

        if use_str is not None:
            if _use is not None:
                use = _use
            else:
                use = _use_dep(use_str[1:-1].split(","), eapi_attrs)
            without_use = Atom(
                blocker_prefix + m.group("without_use"), allow_repo=allow_repo
            )
        else:
            use = None
            if unevaluated_atom is not None and unevaluated_atom.use is not None:
                # unevaluated_atom.use is used for IUSE checks when matching
                # packages, so it must not propagate to without_use
                without_use = Atom(
                    str(self),
                    allow_wildcard=allow_wildcard,
                    allow_repo=allow_repo,
                    eapi=eapi,
                )
            else:
                without_use = self

        self.__dict__["use"] = use
        self.__dict__["without_use"] = without_use

        if unevaluated_atom:
            self.__dict__["unevaluated_atom"] = unevaluated_atom
        else:
            self.__dict__["unevaluated_atom"] = self

        if eapi is not None:
            if not isinstance(eapi, str):
                raise TypeError(
                    "expected eapi argument of "
                    + "%s, got %s: %s"
                    % (
                        str,
                        type(eapi),
                        eapi,
                    )
                )
            if self.slot and not eapi_attrs.slot_deps:
                raise InvalidAtom(
                    _("Slot deps are not allowed in EAPI %s: '%s'") % (eapi, self),
                    category="EAPI.incompatible",
                )
            if self.use:
                if not eapi_attrs.use_deps:
                    raise InvalidAtom(
                        _("Use deps are not allowed in EAPI %s: '%s'") % (eapi, self),
                        category="EAPI.incompatible",
                    )
                elif not eapi_attrs.use_dep_defaults and (
                    self.use.missing_enabled or self.use.missing_disabled
                ):
                    raise InvalidAtom(
                        _("Use dep defaults are not allowed in EAPI %s: '%s'")
                        % (eapi, self),
                        category="EAPI.incompatible",
                    )
                if is_valid_flag is not None and self.use.conditional:
                    invalid_flag = None
                    try:
                        for conditional_type, flags in self.use.conditional.items():
                            for flag in flags:
                                if not is_valid_flag(flag):
                                    invalid_flag = (conditional_type, flag)
                                    raise StopIteration()
                    except StopIteration:
                        pass
                    if invalid_flag is not None:
                        conditional_type, flag = invalid_flag
                        conditional_str = _use_dep._conditional_strings[
                            conditional_type
                        ]
                        msg = _(
                            "USE flag '%s' referenced in "
                            + "conditional '%s' in atom '%s' is not in IUSE"
                        ) % (flag, conditional_str % flag, self)
                        raise InvalidAtom(msg, category="IUSE.missing")
            if (
                self.blocker
                and self.blocker.overlap.forbid
                and not eapi_attrs.strong_blocks
            ):
                raise InvalidAtom(
                    _("Strong blocks are not allowed in EAPI %s: '%s'") % (eapi, self),
                    category="EAPI.incompatible",
                )

    @property
    def slot_operator_built(self) -> bool:
        """
        Returns True if slot_operator == "=" and sub_slot is not None.
        NOTE: foo/bar:2= is unbuilt and returns False, whereas foo/bar:2/2=
        is built and returns True.
        """
        return self.slot_operator == "=" and self.sub_slot is not None

    @property
    def without_repo(self) -> Atom:
        if self.repo is None:
            return self
        return Atom(
            self.replace(_repo_separator + self.repo, "", 1), allow_wildcard=True
        )

    @property
    def without_slot(self) -> Atom:
        if self.slot is None and self.slot_operator is None:
            return self
        atom = remove_slot(self)
        if self.repo is not None:
            atom += _repo_separator + self.repo
        if self.use is not None:
            atom += str(self.use)
        return Atom(atom, allow_repo=True, allow_wildcard=True)

    def with_repo(self, repo: str) -> "Atom":
        atom = remove_slot(self)
        if self.slot is not None or self.slot_operator is not None:
            atom += _slot_separator
            if self.slot is not None:
                atom += self.slot
            if self.sub_slot is not None:
                atom += "/%s" % self.sub_slot
            if self.slot_operator is not None:
                atom += self.slot_operator
        atom += _repo_separator + repo
        if self.use is not None:
            atom += str(self.use)
        return Atom(atom, allow_repo=True, allow_wildcard=True)

    def with_slot(self, slot: str) -> Atom:
        atom = remove_slot(self) + _slot_separator + slot
        if self.repo is not None:
            atom += _repo_separator + self.repo
        if self.use is not None:
            atom += str(self.use)
        return Atom(atom, allow_repo=True, allow_wildcard=True)

    def __setattr__(self, name, value):
        raise AttributeError(
            "Atom instances are immutable", self.__class__, name, value
        )

    def intersects(self, other):
        """
        Atoms with different cpv, operator or use attributes cause this method
        to return False even though there may actually be some intersection.
        TODO: Detect more forms of intersection.
        @param other: The package atom to match
        @type other: Atom
        @rtype: Boolean
        @return: True if this atom and the other atom intersect,
                False otherwise.
        """
        if not isinstance(other, Atom):
            raise TypeError("expected %s, got %s" % (Atom, type(other)))

        if self == other:
            return True

        if (
            self.cp != other.cp
            or self.use != other.use
            or self.operator != other.operator
            or self.cpv != other.cpv
        ):
            return False

        if self.slot is None or other.slot is None or self.slot == other.slot:
            return True

        return False

    def evaluate_conditionals(self, use: frozenset) -> Atom:
        """
        Create an atom instance with any USE conditionals evaluated.
        @param use: The set of enabled USE flags
        @type use: set
        @rtype: Atom
        @return: an atom instance with any USE conditionals evaluated
        """
        if not (self.use and self.use.conditional):
            return self
        atom = remove_slot(self)
        if self.slot is not None or self.slot_operator is not None:
            atom += _slot_separator
            if self.slot is not None:
                atom += self.slot
            if self.sub_slot is not None:
                atom += "/%s" % self.sub_slot
            if self.slot_operator is not None:
                atom += self.slot_operator
        use_dep = self.use.evaluate_conditionals(use)
        atom += str(use_dep)
        return Atom(
            atom,
            unevaluated_atom=self,
            allow_repo=(self.repo is not None),
            _use=use_dep,
        )

    def violated_conditionals(
        self,
        other_use: frozenset,
        is_valid_flag: Callable,
        parent_use: Optional[Any] = None,
    ) -> Atom:
        """
        Create an atom instance with any USE conditional removed, that is
        satisfied by other_use.
        @param other_use: The set of enabled USE flags
        @type other_use: set
        @param is_valid_flag: Function that decides if a use flag is referenceable in use deps
        @type is_valid_flag: function
        @param parent_use: Set of enabled use flags of the package requiring this atom
        @type parent_use: set
        @rtype: Atom
        @return: an atom instance with any satisfied USE conditionals removed
        """
        if not self.use:
            return self
        atom = remove_slot(self)
        if self.slot is not None or self.slot_operator is not None:
            atom += _slot_separator
            if self.slot is not None:
                atom += self.slot
            if self.sub_slot is not None:
                atom += "/%s" % self.sub_slot
            if self.slot_operator is not None:
                atom += self.slot_operator
        use_dep = self.use.violated_conditionals(other_use, is_valid_flag, parent_use)
        atom += str(use_dep)
        return Atom(
            atom,
            unevaluated_atom=self,
            allow_repo=(self.repo is not None),
            _use=use_dep,
        )

    def _eval_qa_conditionals(self, use_mask, use_force):
        if not (self.use and self.use.conditional):
            return self
        atom = remove_slot(self)
        if self.slot is not None or self.slot_operator is not None:
            atom += _slot_separator
            if self.slot is not None:
                atom += self.slot
            if self.sub_slot is not None:
                atom += "/%s" % self.sub_slot
            if self.slot_operator is not None:
                atom += self.slot_operator
        use_dep = self.use._eval_qa_conditionals(use_mask, use_force)
        atom += str(use_dep)
        return Atom(
            atom,
            unevaluated_atom=self,
            allow_repo=(self.repo is not None),
            _use=use_dep,
        )

    def __copy__(self):
        """Immutable, so returns self."""
        return self

    def __deepcopy__(self, memo: Dict[int, Any] = None) -> Atom:
        """Immutable, so returns self."""
        memo[id(self)] = self
        return self

    def match(self, pkg: Package) -> bool:
        """
        Check if the given package instance matches this atom.

        @param pkg: a Package instance
        @type pkg: Package
        @return: True if this atom matches pkg, otherwise False
        @rtype: bool
        """
        return bool(match_from_list(self, (pkg,)))

class ExtendedAtomDict(portage.cache.mappings.MutableMapping):
    """
    dict() wrapper that supports extended atoms as keys and allows lookup
    of a normal cp against other normal cp and extended cp.
    The value type has to be given to __init__ and is assumed to be the same
    for all values.
    """

    __slots__ = ("_extended", "_normal", "_value_class")

    def __init__(self, value_class: type) -> None:
        self._extended = {}
        self._normal = {}
        self._value_class = value_class

    def copy(self):
        result = self.__class__(self._value_class)
        result._extended.update(self._extended)
        result._normal.update(self._normal)
        return result

    def __iter__(self):
        for k in self._normal:
            yield k
        for k in self._extended:
            yield k

    def iteritems(self) -> Iterator[Union[Iterator, Iterator[Tuple[str, List[Atom]]]]]:
        try:
            for item in self._normal.items():
                yield item
            for item in self._extended.items():
                yield item
        except AttributeError:
            pass  # FEATURES=python-trace

    def __delitem__(self, cp: str) -> Optional[Any]:
        if "*" in cp:
            return self._extended.__delitem__(cp)
        return self._normal.__delitem__(cp)

    keys = __iter__
    items = iteritems

    def __len__(self):
        return len(self._normal) + len(self._extended)

    def setdefault(
        self, cp: str, default: Union[Dict, List, Set] = None
    ) -> Union[Dict, List[Atom], Set]:
        if "*" in cp:
            return self._extended.setdefault(cp, default)
        return self._normal.setdefault(cp, default)

    def __getitem__(self, cp: str) -> Any:

        if not isinstance(cp, str):
            raise KeyError(cp)

        if "*" in cp:
            return self._extended[cp]

        ret = self._value_class()
        normal_match = self._normal.get(cp)
        match = False

        if normal_match is not None:
            match = True
            if hasattr(ret, "update"):
                ret.update(normal_match)
            elif hasattr(ret, "extend"):
                ret.extend(normal_match)
            else:
                raise NotImplementedError()

        for extended_cp in self._extended:
            if extended_cp_match(extended_cp, cp):
                match = True
                if hasattr(ret, "update"):
                    ret.update(self._extended[extended_cp])
                elif hasattr(ret, "extend"):
                    ret.extend(self._extended[extended_cp])
                else:
                    raise NotImplementedError()

        if not match:
            raise KeyError(cp)

        return ret

    def __setitem__(self, cp: str, val: Tuple[Atom, ...]) -> None:
        if "*" in cp:
            self._extended[cp] = val
        else:
            self._normal[cp] = val

    def __eq__(self, other):
        return (
            self._value_class == other._value_class
            and self._extended == other._extended
            and self._normal == other._normal
        )

    def clear(self) -> None:
        self._extended.clear()
        self._normal.clear()
