# deps.py -- Portage dependency resolution functions
# Copyright 2003-2021 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

__all__ = [
    "Atom",
    "best_match_to_list",
    "cpvequal",
    "dep_getcpv",
    "dep_getkey",
    "dep_getslot",
    "dep_getusedeps",
    "dep_opconvert",
    "flatten",
    "get_operator",
    "isjustname",
    "isspecific",
    "isvalidatom",
    "match_from_list",
    "match_to_list",
    "paren_enclose",
    "paren_normalize",
    "paren_reduce",
    "remove_slot",
    "strip_empty",
    "use_reduce",
    "_repo_separator",
    "_slot_separator",
]

import re
import sys
import warnings
from functools import lru_cache
from re import Pattern
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple, Union

import portage
from portage.eapi import _eapi_attrs

portage.proxy.lazyimport.lazyimport(
    globals(),
    "_emerge.Package:Package",
    "portage.cache.mappings",
    "portage.exception:InvalidAtom,InvalidData,InvalidDependString",
    "portage.util:cmp_sort_key,writemsg",
    "portage.versions:_pkg_str",
)

from portage import _encodings, _unicode_decode, _unicode_encode
from portage.dep.atom import Atom
from portage.eapi import _get_eapi_attrs
from portage.localization import _
from portage.versions import (
    _cp,
    _cpv,
    _slot,
    _unknown_repo,
    _vr,
    catpkgsplit,
    vercmp,
    ververify,
)

# \w is [a-zA-Z0-9_]

# PMS 3.1.3: A slot name may contain any of the characters [A-Za-z0-9+_.-].
# It must not begin with a hyphen or a dot.
_slot_separator = ":"
# loosly match SLOT, which may have an optional ABI part
_slot_loose = r"([\w+./*=-]+)"

_use = r"\[.*\]"
_op = r"([=~]|[><]=?)"

_repo_separator = "::"
_repo_name = r"[\w][\w-]*"
_repo_name_re = re.compile("^" + _repo_name + "$", re.UNICODE)
_repo = r"(?:" + _repo_separator + "(" + _repo_name + ")" + ")?"

_extended_cat = r"[\w+*][\w+.*-]*"

_slot_dep_re_cache = {}


def _get_slot_dep_re(eapi_attrs: _eapi_attrs) -> Pattern:
    cache_key = eapi_attrs.slot_operator
    slot_re = _slot_dep_re_cache.get(cache_key)
    if slot_re is not None:
        return slot_re

    if eapi_attrs.slot_operator:
        slot_re = _slot + r"?(\*|=|/" + _slot + r"=?)?"
    else:
        slot_re = _slot

    slot_re = re.compile("^" + slot_re + "$", re.VERBOSE | re.UNICODE)

    _slot_dep_re_cache[cache_key] = slot_re
    return slot_re


def _match_slot(atom: Atom, pkg: Union[Package, _pkg_str]) -> bool:
    if pkg.slot == atom.slot:
        if not atom.sub_slot:
            return True
        if atom.sub_slot == pkg.sub_slot:
            return True
    return False


_atom_re = None


def _get_atom_re(eapi_attrs: _eapi_attrs) -> Pattern:
    global _atom_re
    if _atom_re is not None:
        return _atom_re

    cp_re = _cp
    cpv_re = _cpv

    _atom_re = re.compile(
        "^(?P<without_use>(?:"
        + "(?P<op>"
        + _op
        + cpv_re
        + ")|"
        + "(?P<star>="
        + cpv_re
        + r"\*)|"
        + "(?P<simple>"
        + cp_re
        + "))"
        + "("
        + _slot_separator
        + _slot_loose
        + ")?"
        + _repo
        + ")("
        + _use
        + ")?$",
        re.VERBOSE | re.UNICODE,
    )
    return _atom_re


_atom_wildcard_re = None


def _get_atom_wildcard_re(eapi_attrs: _eapi_attrs) -> Pattern:
    global _atom_wildcard_re
    if _atom_wildcard_re is not None:
        return _atom_wildcard_re

    pkg_re = r"[\w+*][\w+*-]*?"

    _atom_wildcard_re = re.compile(
        r"((?P<simple>("
        + _extended_cat
        + r")/("
        + pkg_re
        + r"(-"
        + _vr
        + ")?))"
        + "|(?P<star>=(("
        + _extended_cat
        + r")/("
        + pkg_re
        + r"))-(?P<version>\*\w+\*)))"
        + "(:(?P<slot>"
        + _slot_loose
        + r"))?("
        + _repo_separator
        + r"(?P<repo>"
        + _repo_name
        + r"))?$",
        re.UNICODE,
    )
    return _atom_wildcard_re


_usedep_re = None


def _get_usedep_re(eapi_attrs: _eapi_attrs) -> Pattern:
    """
    @param eapi_attrs: The EAPI attributes from _get_eapi_attrs
    @type eapi_attrs: _eapi_attrs
    @rtype: regular expression object
    @return: A regular expression object that matches valid USE deps for the
            given eapi.
    """
    global _usedep_re
    if _usedep_re is not None:
        return _usedep_re

    _usedep_re = re.compile(
        r"^(?P<prefix>[!-]?)(?P<flag>"
        + r"[A-Za-z0-9][A-Za-z0-9+_@-]*"
        + r")(?P<default>(\(\+\)|\(\-\))?)(?P<suffix>[?=]?)$"
    )
    return _usedep_re


_useflag_re = None


def _get_useflag_re(eapi: Optional[str]) -> Pattern:
    """
    When eapi is None then validation is not as strict, since we want the
    same to work for multiple EAPIs that may have slightly different rules.
    @param eapi: The EAPI
    @type eapi: String or None
    @rtype: regular expression object
    @return: A regular expression object that matches valid USE flags for the
            given eapi.
    """
    global _useflag_re
    if _useflag_re is not None:
        return _useflag_re

    _useflag_re = re.compile(r"^[A-Za-z0-9][A-Za-z0-9+_@-]*$")
    return _useflag_re


def cpvequal(cpv1: Union[_pkg_str, str], cpv2: Union[_pkg_str, str]) -> bool:
    """
    Example Usage:
            >>> from portage.dep import cpvequal
            >>> cpvequal("sys-apps/portage-2.1","sys-apps/portage-2.1")
            >>> True

    @param cpv1: CategoryPackageVersion (no operators) Example: "sys-apps/portage-2.1"
    @type cpv1: String
    @param cpv2: CategoryPackageVersion (no operators) Example: "sys-apps/portage-2.1"
    @type cpv2: String
    @rtype: Boolean
    @return:
            1.  True if cpv1 = cpv2
            2.  False Otherwise
            3.  Throws PortageException if cpv1 or cpv2 is not a CPV
    """

    try:
        try:
            split1 = cpv1.cpv_split
        except AttributeError:
            cpv1 = _pkg_str(cpv1)
            split1 = cpv1.cpv_split

        try:
            split2 = cpv2.cpv_split
        except AttributeError:
            cpv2 = _pkg_str(cpv2)
            split2 = cpv2.cpv_split

    except InvalidData:
        raise portage.exception.PortageException(
            _("Invalid data '%s, %s', parameter was not a CPV") % (cpv1, cpv2)
        )

    if split1[0] != split2[0] or split1[1] != split2[1]:
        return False

    return vercmp(cpv1.version, cpv2.version) == 0


def strip_empty(myarr):
    """
    Strip all empty elements from an array

    @param myarr: The list of elements
    @type myarr: List
    @rtype: Array
    @return: The array with empty elements removed
    """
    warnings.warn(
        _("%s is deprecated and will be removed without replacement.")
        % ("portage.dep.strip_empty",),
        DeprecationWarning,
        stacklevel=2,
    )
    return [x for x in myarr if x]


def paren_reduce(mystr, _deprecation_warn=True):
    """
    Take a string and convert all paren enclosed entities into sublists and
    split the list elements by spaces. All redundant brackets are removed.

    Example usage:
            >>> paren_reduce('foobar foo? ( bar baz )')
            ['foobar', 'foo?', ['bar', 'baz']]

    @param mystr: The string to reduce
    @type mystr: String
    @rtype: Array
    @return: The reduced string in an array
    """
    if portage._internal_caller and _deprecation_warn:
        warnings.warn(
            _("%s is deprecated and will be removed without replacement.")
            % ("portage.dep.paren_reduce",),
            DeprecationWarning,
            stacklevel=2,
        )
    mysplit = mystr.split()
    level = 0
    stack = [[]]
    need_bracket = False

    for token in mysplit:
        if token == "(":
            need_bracket = False
            stack.append([])
            level += 1
        elif token == ")":
            if need_bracket:
                raise InvalidDependString(_("malformed syntax: '%s'") % mystr)
            if level > 0:
                level -= 1
                l = stack.pop()
                is_single = len(l) == 1 or (
                    len(l) == 2 and (l[0] == "||" or l[0][-1] == "?")
                )

                def ends_in_any_of_dep(k):
                    return k >= 0 and stack[k] and stack[k][-1] == "||"

                def ends_in_operator(k):
                    return (
                        k >= 0
                        and stack[k]
                        and (stack[k][-1] == "||" or stack[k][-1][-1] == "?")
                    )

                def special_append():
                    """
                    Use extend instead of append if possible. This kills all redundant brackets.
                    """
                    if is_single and (
                        not stack[level] or not stack[level][-1][-1] == "?"
                    ):
                        if len(l) == 1 and isinstance(l[0], list):
                            # l = [[...]]
                            stack[level].extend(l[0])
                        else:
                            stack[level].extend(l)
                    else:
                        stack[level].append(l)

                if l:
                    if not ends_in_any_of_dep(level - 1) and not ends_in_operator(
                        level
                    ):
                        # Optimize: ( ( ... ) ) -> ( ... ). Make sure there is no '||' hanging around.
                        stack[level].extend(l)
                    elif not stack[level]:
                        # An '||' in the level above forces us to keep to brackets.
                        special_append()
                    elif len(l) == 1 and ends_in_any_of_dep(level):
                        # Optimize: || ( A ) -> A
                        stack[level].pop()
                        special_append()
                    elif (
                        len(l) == 2
                        and (l[0] == "||" or l[0][-1] == "?")
                        and stack[level][-1] in (l[0], "||")
                    ):
                        # Optimize: 	|| ( || ( ... ) ) -> || ( ... )
                        # 			foo? ( foo? ( ... ) ) -> foo? ( ... )
                        # 			|| ( foo? ( ... ) ) -> foo? ( ... )
                        stack[level].pop()
                        special_append()
                    else:
                        special_append()
                else:
                    if stack[level] and (
                        stack[level][-1] == "||" or stack[level][-1][-1] == "?"
                    ):
                        stack[level].pop()
            else:
                raise InvalidDependString(_("malformed syntax: '%s'") % mystr)
        elif token == "||":
            if need_bracket:
                raise InvalidDependString(_("malformed syntax: '%s'") % mystr)
            need_bracket = True
            stack[level].append(token)
        else:
            if need_bracket:
                raise InvalidDependString(_("malformed syntax: '%s'") % mystr)

            if token[-1] == "?":
                need_bracket = True

            stack[level].append(token)

    if level != 0 or need_bracket:
        raise InvalidDependString(_("malformed syntax: '%s'") % mystr)

    return stack[0]


class paren_normalize(list):
    """Take a dependency structure as returned by paren_reduce or use_reduce
    and generate an equivalent structure that has no redundant lists."""

    def __init__(self, src):
        if portage._internal_caller:
            warnings.warn(
                _("%s is deprecated and will be removed without replacement.")
                % ("portage.dep.paren_normalize",),
                DeprecationWarning,
                stacklevel=2,
            )
        list.__init__(self)
        self._zap_parens(src, self)

    def _zap_parens(self, src, dest, disjunction=False):
        if not src:
            return dest
        i = iter(src)
        for x in i:
            if isinstance(x, str):
                if x in ("||", "^^"):
                    y = self._zap_parens(next(i), [], disjunction=True)
                    if len(y) == 1:
                        dest.append(y[0])
                    else:
                        dest.append(x)
                        dest.append(y)
                elif x.endswith("?"):
                    dest.append(x)
                    dest.append(self._zap_parens(next(i), []))
                else:
                    dest.append(x)
            else:
                if disjunction:
                    x = self._zap_parens(x, [])
                    if len(x) == 1:
                        dest.append(x[0])
                    else:
                        dest.append(x)
                else:
                    self._zap_parens(x, dest)
        return dest


def paren_enclose(
    mylist: List[str], unevaluated_atom: bool = False, opconvert: bool = False
) -> str:
    """
    Convert a list to a string with sublists enclosed with parens.

    @param mylist: The list
    @type mylist: List
    @rtype: String
    @return: The paren enclosed string

    Example usage:
            >>> test = ['foobar','foo',['bar','baz']]
            >>> paren_enclose(test)
            'foobar foo ( bar baz )'
    """
    mystrparts = []
    for x in mylist:
        if isinstance(x, list):
            if opconvert and x and x[0] == "||":
                mystrparts.append("%s ( %s )" % (x[0], paren_enclose(x[1:])))
            else:
                mystrparts.append("( %s )" % paren_enclose(x))
        else:
            if unevaluated_atom:
                x = getattr(x, "unevaluated_atom", x)
            mystrparts.append(x)
    return " ".join(mystrparts)


@lru_cache(1024)
def _use_reduce_cached(
    depstr: str,
    uselist: frozenset,
    masklist: frozenset,
    matchall: bool,
    excludeall: frozenset,
    is_src_uri: bool,
    eapi: Optional[str],
    opconvert: bool,
    flat: bool,
    is_valid_flag: Optional[Callable],
    token_class: Optional[type],
    matchnone: bool,
    subset: Optional[Any],
) -> Union[List[Union[List[Atom], Atom, str]], List[Atom], List[str]]:
    if opconvert and flat:
        raise ValueError(
            "portage.dep.use_reduce: 'opconvert' and 'flat' are mutually exclusive"
        )

    if matchall and matchnone:
        raise ValueError(
            "portage.dep.use_reduce: 'matchall' and 'matchnone' are mutually exclusive"
        )

    eapi_attrs = _get_eapi_attrs(eapi)
    useflag_re = _get_useflag_re(eapi)

    def is_active(conditional: str) -> bool:
        """
        Decides if a given use conditional is active.
        """
        if conditional.startswith("!"):
            flag = conditional[1:-1]
            is_negated = True
        else:
            flag = conditional[:-1]
            is_negated = False

        if is_valid_flag:
            if not is_valid_flag(flag):
                msg = _(
                    "USE flag '%s' referenced in " + "conditional '%s' is not in IUSE"
                ) % (flag, conditional)
                e = InvalidData(msg, category="IUSE.missing")
                raise InvalidDependString(msg, errors=(e,))
        else:
            if useflag_re.match(flag) is None:
                raise InvalidDependString(
                    _("invalid use flag '%s' in conditional '%s'") % (flag, conditional)
                )

        if is_negated and flag in excludeall:
            return False

        if flag in masklist:
            return is_negated

        if matchall:
            return True

        if matchnone:
            return False

        return (flag in uselist and not is_negated) or (
            flag not in uselist and is_negated
        )

    if subset:

        def select_subset(dep_struct, disjunction, selected):
            result = []
            stack = list(dep_struct)
            stack.reverse()
            while stack:
                token = stack.pop()
                try:
                    conditional = token.endswith("?")
                except AttributeError:
                    if disjunction:
                        children = select_subset(token, False, selected)
                        if children:
                            result.append(children)
                    else:
                        result.extend(select_subset(token, False, selected))
                else:
                    if conditional:
                        children = stack.pop()
                        if is_active(token):
                            if disjunction:
                                children = select_subset(
                                    children, False, selected or token[:-1] in subset
                                )
                                if children:
                                    result.append(children)
                            else:
                                result.extend(
                                    select_subset(
                                        children,
                                        False,
                                        selected or token[:-1] in subset,
                                    )
                                )
                    elif token == "||":
                        children = select_subset(stack.pop(), True, selected)
                        if children:
                            if disjunction:
                                result.extend(children)
                            else:
                                result.append(token)
                                result.append(children)
                    elif selected:
                        result.append(token)
            return result

        depstr = paren_enclose(
            select_subset(paren_reduce(depstr, _deprecation_warn=False), False, False)
        )

    def missing_white_space_check(token, pos):
        """
        Used to generate good error messages for invalid tokens.
        """
        for x in (")", "(", "||"):
            if token.startswith(x) or token.endswith(x):
                raise InvalidDependString(
                    _("missing whitespace around '%s' at '%s', token %s")
                    % (x, token, pos + 1)
                )

    mysplit = depstr.split()
    # Count the bracket level.
    level = 0
    # We parse into a stack. Every time we hit a '(', a new empty list is appended to the stack.
    # When we hit a ')', the last list in the stack is merged with list one level up.
    stack = [[]]
    # Set need_bracket to True after use conditionals or ||. Other tokens need to ensure
    # that need_bracket is not True.
    need_bracket = False
    # Set need_simple_token to True after a SRC_URI arrow. Other tokens need to ensure
    # that need_simple_token is not True.
    need_simple_token = False

    for pos, token in enumerate(mysplit):
        if token == "(":
            if need_simple_token:
                raise InvalidDependString(
                    _("expected: file name, got: '%s', token %s") % (token, pos + 1)
                )
            if len(mysplit) >= pos + 2 and mysplit[pos + 1] == ")":
                raise InvalidDependString(
                    _("expected: dependency string, got: ')', token %s") % (pos + 1,)
                )
            need_bracket = False
            stack.append([])
            level += 1
        elif token == ")":
            if need_bracket:
                raise InvalidDependString(
                    _("expected: '(', got: '%s', token %s") % (token, pos + 1)
                )
            if need_simple_token:
                raise InvalidDependString(
                    _("expected: file name, got: '%s', token %s") % (token, pos + 1)
                )
            if level > 0:
                level -= 1
                l = stack.pop()

                is_single = (
                    len(l) == 1
                    or (opconvert and l and l[0] == "||")
                    or (not opconvert and len(l) == 2 and l[0] == "||")
                )
                ignore = False

                if flat:
                    # In 'flat' mode, we simply merge all lists into a single large one.
                    if stack[level] and stack[level][-1][-1] == "?":
                        # The last token before the '(' that matches the current ')'
                        # was a use conditional. The conditional is removed in any case.
                        # Merge the current list if needed.
                        if is_active(stack[level][-1]):
                            stack[level].pop()
                            stack[level].extend(l)
                        else:
                            stack[level].pop()
                    else:
                        stack[level].extend(l)
                    continue

                if stack[level] and isinstance(stack[level][-1], str):
                    if stack[level][-1] == "||" and not l:
                        # Optimize: || ( ) -> .
                        if not eapi_attrs.empty_groups_always_true:
                            # in EAPI 7+, we need to fail here
                            l.append((token_class or str)("__const__/empty-any-of"))
                        stack[level].pop()
                    elif stack[level][-1][-1] == "?":
                        # The last token before the '(' that matches the current ')'
                        # was a use conditional, remove it and decide if we
                        # have to keep the current list.
                        if not is_active(stack[level][-1]):
                            ignore = True
                        stack[level].pop()

                def ends_in_any_of_dep(k: int) -> Union[List, bool]:
                    return k >= 0 and stack[k] and stack[k][-1] == "||"

                def starts_with_any_of_dep(k):
                    # 'ends_in_any_of_dep' for opconvert
                    return k >= 0 and stack[k] and stack[k][0] == "||"

                def last_any_of_operator_level(k: int) -> int:
                    # Returns the level of the last || operator if it is in effect for
                    # the current level. It is not in effect, if there is a level, that
                    # ends in a non-operator. This is almost equivalent to stack[level][-1]=="||",
                    # expect that it skips empty levels.
                    while k >= 0:
                        if stack[k] and isinstance(stack[k][-1], str):
                            if stack[k][-1] == "||":
                                return k
                            if stack[k][-1][-1] != "?":
                                return -1
                        k -= 1
                    return -1

                def special_append() -> None:
                    """
                    Use extend instead of append if possible. This kills all redundant brackets.
                    """
                    if is_single:
                        # Either [A], [[...]] or [|| [...]]
                        if l[0] == "||" and ends_in_any_of_dep(level - 1):
                            if opconvert:
                                stack[level].extend(l[1:])
                            else:
                                stack[level].extend(l[1])
                        elif len(l) == 1 and isinstance(l[0], list):
                            # l = [[...]]
                            last = last_any_of_operator_level(level - 1)
                            if last == -1:
                                if (
                                    opconvert
                                    and isinstance(l[0], list)
                                    and l[0]
                                    and l[0][0] == "||"
                                ):
                                    stack[level].append(l[0])
                                else:
                                    stack[level].extend(l[0])
                            else:
                                if opconvert and l[0] and l[0][0] == "||":
                                    stack[level].extend(l[0][1:])
                                else:
                                    stack[level].append(l[0])
                        else:
                            stack[level].extend(l)
                    else:
                        if opconvert and stack[level] and stack[level][-1] == "||":
                            stack[level][-1] = ["||"] + l
                        else:
                            stack[level].append(l)

                if l and not ignore:
                    # The current list is not empty and we don't want to ignore it because
                    # of an inactive use conditional.
                    if not ends_in_any_of_dep(level - 1) and not ends_in_any_of_dep(
                        level
                    ):
                        # Optimize: ( ( ... ) ) -> ( ... ). Make sure there is no '||' hanging around.
                        stack[level].extend(l)
                    elif not stack[level]:
                        # An '||' in the level above forces us to keep to brackets.
                        special_append()
                    elif is_single and ends_in_any_of_dep(level):
                        # Optimize: || ( A ) -> A,  || ( || ( ... ) ) -> || ( ... )
                        stack[level].pop()
                        special_append()
                    elif ends_in_any_of_dep(level) and ends_in_any_of_dep(level - 1):
                        # Optimize: || ( A || ( B C ) ) -> || ( A B C )
                        stack[level].pop()
                        stack[level].extend(l)
                    else:
                        if opconvert and ends_in_any_of_dep(level):
                            # In opconvert mode, we have to move the operator from the level
                            # above into the current list.
                            stack[level].pop()
                            stack[level].append(["||"] + l)
                        else:
                            special_append()

            else:
                raise InvalidDependString(
                    _("no matching '%s' for '%s', token %s") % ("(", ")", pos + 1)
                )
        elif token == "||":
            if is_src_uri:
                raise InvalidDependString(
                    _("any-of dependencies are not allowed in SRC_URI: token %s")
                    % (pos + 1,)
                )
            if need_bracket:
                raise InvalidDependString(
                    _("expected: '(', got: '%s', token %s") % (token, pos + 1)
                )
            need_bracket = True
            stack[level].append(token)
        elif token == "->":
            if need_simple_token:
                raise InvalidDependString(
                    _("expected: file name, got: '%s', token %s") % (token, pos + 1)
                )
            if not is_src_uri:
                raise InvalidDependString(
                    _("SRC_URI arrow are only allowed in SRC_URI: token %s")
                    % (pos + 1,)
                )
            if not eapi_attrs.src_uri_arrows:
                raise InvalidDependString(
                    _("SRC_URI arrow not allowed in EAPI %s: token %s")
                    % (eapi, pos + 1)
                )
            need_simple_token = True
            stack[level].append(token)
        else:
            if need_bracket:
                raise InvalidDependString(
                    _("expected: '(', got: '%s', token %s") % (token, pos + 1)
                )

            if need_simple_token and "/" in token:
                # The last token was a SRC_URI arrow, make sure we have a simple file name.
                raise InvalidDependString(
                    _("expected: file name, got: '%s', token %s") % (token, pos + 1)
                )

            if token[-1] == "?":
                need_bracket = True
            else:
                need_simple_token = False
                if is_src_uri:
                    if (
                        not eapi_attrs.selective_src_uri_restriction
                        and token.startswith(("fetch+", "mirror+"))
                    ):
                        raise InvalidDependString(
                            _(
                                "Selective fetch/mirror restriction not allowed "
                                "in EAPI %s: token %s"
                            )
                            % (eapi, pos + 1)
                        )
                elif token_class:
                    # Add a hack for SRC_URI here, to avoid conditional code at the consumer level
                    try:
                        token = token_class(
                            token, eapi=eapi, is_valid_flag=is_valid_flag
                        )
                    except InvalidAtom as e:
                        missing_white_space_check(token, pos)
                        raise InvalidDependString(
                            _("Invalid atom (%s), token %s") % (e, pos + 1), errors=(e,)
                        )
                    except SystemExit:
                        raise
                    except Exception as e:
                        missing_white_space_check(token, pos)
                        raise InvalidDependString(
                            _("Invalid token '%s', token %s") % (token, pos + 1)
                        )

                    if not matchall and hasattr(token, "evaluate_conditionals"):
                        token = token.evaluate_conditionals(uselist)

            stack[level].append(token)

    if level != 0:
        raise InvalidDependString(_("Missing '%s' at end of string") % (")",))

    if need_bracket:
        raise InvalidDependString(_("Missing '%s' at end of string") % ("(",))

    if need_simple_token:
        raise InvalidDependString(_("Missing file name at end of string"))

    return stack[0]


def use_reduce(
    depstr: str,
    uselist: Union[List, Tuple[()], frozenset] = (),
    masklist: Tuple[()] = (),
    matchall: bool = False,
    excludeall: Tuple[()] = (),
    is_src_uri: bool = False,
    eapi: Optional[str] = None,
    opconvert: bool = False,
    flat: bool = False,
    is_valid_flag: Optional[Callable] = None,
    token_class: Optional[type] = None,
    matchnone: bool = False,
    subset: Optional[Any] = None,
) -> Union[
    List[Union[List[List[Atom]], List[Atom], str]],
    List[Union[List[Atom], Atom, str]],
    List[Atom],
]:
    """
    Takes a dep string and reduces the use? conditionals out, leaving an array
    with subarrays. All redundant brackets are removed.

    @param depstr: depstring
    @type depstr: String
    @param uselist: Sequence of use enabled flags
    @type uselist: Sequence
    @param masklist: Sequence of masked flags (always treated as disabled)
    @type masklist: Sequence
    @param matchall: Treat all conditionals as active. Used by repoman.
    @type matchall: Bool
    @param excludeall: Sequence of flags for which negated conditionals are always treated as inactive.
    @type excludeall: Sequence
    @param is_src_uri: Indicates if depstr represents a SRC_URI
    @type is_src_uri: Bool
    @param eapi: Indicates the EAPI the dep string has to comply to
    @type eapi: String
    @param opconvert: Put every operator as first element into it's argument list
    @type opconvert: Bool
    @param flat: Create a flat list of all tokens
    @type flat: Bool
    @param is_valid_flag: Function that decides if a given use flag might be used in use conditionals
    @type is_valid_flag: Function
    @param token_class: Convert all non operator tokens into this class
    @type token_class: Class
    @param matchnone: Treat all conditionals as inactive. Used by digestgen().
    @type matchnone: Bool
    @param subset: Select a subset of dependencies conditional on the given flags
    @type subset: Sequence
    @rtype: List
    @return: The use reduced depend array
    """
    if isinstance(depstr, list):
        if portage._internal_caller:
            warnings.warn(
                _(
                    "Passing paren_reduced dep arrays to %s is deprecated. "
                    + "Pass the original dep string instead."
                )
                % ("portage.dep.use_reduce",),
                DeprecationWarning,
                stacklevel=2,
            )
        depstr = paren_enclose(depstr)

    if uselist is not None:
        uselist = frozenset(uselist)
    if masklist is not None:
        masklist = frozenset(masklist)
    if excludeall is not None:
        excludeall = frozenset(excludeall)
    if subset is not None:
        subset = frozenset(subset)

    result = _use_reduce_cached(
        depstr,
        uselist,
        masklist,
        matchall,
        excludeall,
        is_src_uri,
        eapi,
        opconvert,
        flat,
        is_valid_flag,
        token_class,
        matchnone,
        subset,
    )

    # The list returned by this function may be modified, so return a copy.
    return result[:]


def dep_opconvert(deplist):
    """
    Iterate recursively through a list of deps, if the
    dep is a '||' or '&&' operator, combine it with the
    list of deps that follows..

    Example usage:
            >>> test = ["blah", "||", ["foo", "bar", "baz"]]
            >>> dep_opconvert(test)
            ['blah', ['||', 'foo', 'bar', 'baz']]

    @param deplist: A list of deps to format
    @type deplist: List
    @rtype: List
    @return:
            The new list with the new ordering
    """
    if portage._internal_caller:
        warnings.warn(
            _(
                "%s is deprecated. Use %s with the opconvert parameter set to True instead."
            )
            % ("portage.dep.dep_opconvert", "portage.dep.use_reduce"),
            DeprecationWarning,
            stacklevel=2,
        )

    retlist = []
    x = 0
    while x != len(deplist):
        if isinstance(deplist[x], list):
            retlist.append(dep_opconvert(deplist[x]))
        elif deplist[x] == "||":
            retlist.append([deplist[x]] + dep_opconvert(deplist[x + 1]))
            x += 1
        else:
            retlist.append(deplist[x])
        x += 1
    return retlist


def flatten(mylist):
    """
    Recursively traverse nested lists and return a single list containing
    all non-list elements that are found.

    @param mylist: A list containing nested lists and non-list elements.
    @type mylist: List
    @rtype: List
    @return: A single list containing only non-list elements.

    Example usage:
            >>> flatten([1, [2, 3, [4]]])
            [1, 2, 3, 4]
    """
    if portage._internal_caller:
        warnings.warn(
            _("%s is deprecated and will be removed without replacement.")
            % ("portage.dep.flatten",),
            DeprecationWarning,
            stacklevel=2,
        )

    newlist = []
    for x in mylist:
        if isinstance(x, list):
            newlist.extend(flatten(x))
        else:
            newlist.append(x)
    return newlist


class _use_dep:

    __slots__ = (
        "_eapi_attrs",
        "conditional",
        "missing_enabled",
        "missing_disabled",
        "disabled",
        "enabled",
        "tokens",
        "required",
    )

    class _conditionals_class:
        __slots__ = ("enabled", "disabled", "equal", "not_equal")

        def items(self) -> Iterator[Union[Iterator, Iterator[Tuple[str, frozenset]]]]:
            for k in self.__slots__:
                v = getattr(self, k, None)
                if v:
                    yield (k, v)

        def values(self):
            for k in self.__slots__:
                v = getattr(self, k, None)
                if v:
                    yield v

    # used in InvalidAtom messages
    _conditional_strings = {
        "enabled": "%s?",
        "disabled": "!%s?",
        "equal": "%s=",
        "not_equal": "!%s=",
    }

    def __init__(
        self,
        use: List[str],
        eapi_attrs: _eapi_attrs,
        enabled_flags: Optional[Set[str]] = None,
        disabled_flags: Optional[Set[str]] = None,
        missing_enabled: Optional[frozenset] = None,
        missing_disabled: Optional[frozenset] = None,
        conditional: Optional[Any] = None,
        required: Optional[frozenset] = None,
    ) -> None:

        self._eapi_attrs = eapi_attrs

        if enabled_flags is not None:
            # A shortcut for the classe's own methods.
            self.tokens = use
            if not isinstance(self.tokens, tuple):
                self.tokens = tuple(self.tokens)

            self.required = frozenset(required)
            self.enabled = frozenset(enabled_flags)
            self.disabled = frozenset(disabled_flags)
            self.missing_enabled = frozenset(missing_enabled)
            self.missing_disabled = frozenset(missing_disabled)
            self.conditional = None

            if conditional:
                self.conditional = self._conditionals_class()
                for k in "enabled", "disabled", "equal", "not_equal":
                    setattr(self.conditional, k, frozenset(conditional.get(k, [])))

            return

        enabled_flags = set()
        disabled_flags = set()
        missing_enabled = set()
        missing_disabled = set()
        no_default = set()

        conditional = {}
        usedep_re = _get_usedep_re(self._eapi_attrs)

        for x in use:
            m = usedep_re.match(x)
            if m is None:
                raise InvalidAtom(_("Invalid use dep: '%s'") % (x,))

            operator = m.group("prefix") + m.group("suffix")
            flag = m.group("flag")
            default = m.group("default")

            if not operator:
                enabled_flags.add(flag)
            elif operator == "-":
                disabled_flags.add(flag)
            elif operator == "?":
                conditional.setdefault("enabled", set()).add(flag)
            elif operator == "=":
                conditional.setdefault("equal", set()).add(flag)
            elif operator == "!=":
                conditional.setdefault("not_equal", set()).add(flag)
            elif operator == "!?":
                conditional.setdefault("disabled", set()).add(flag)
            else:
                raise InvalidAtom(_("Invalid use dep: '%s'") % (x,))

            if default:
                if default == "(+)":
                    if flag in missing_disabled or flag in no_default:
                        raise InvalidAtom(_("Invalid use dep: '%s'") % (x,))
                    missing_enabled.add(flag)
                else:
                    if flag in missing_enabled or flag in no_default:
                        raise InvalidAtom(_("Invalid use dep: '%s'") % (x,))
                    missing_disabled.add(flag)
            else:
                if flag in missing_enabled or flag in missing_disabled:
                    raise InvalidAtom(_("Invalid use dep: '%s'") % (x,))
                no_default.add(flag)

        self.tokens = use
        if not isinstance(self.tokens, tuple):
            self.tokens = tuple(self.tokens)

        self.required = frozenset(no_default)

        self.enabled = frozenset(enabled_flags)
        self.disabled = frozenset(disabled_flags)
        self.missing_enabled = frozenset(missing_enabled)
        self.missing_disabled = frozenset(missing_disabled)
        self.conditional = None

        if conditional:
            self.conditional = self._conditionals_class()
            for k in "enabled", "disabled", "equal", "not_equal":
                setattr(self.conditional, k, frozenset(conditional.get(k, ())))

    def __bool__(self) -> bool:
        return bool(self.tokens)

    def __str__(self) -> str:
        if not self.tokens:
            return ""
        return "[%s]" % (",".join(self.tokens),)

    def __repr__(self):
        return "portage.dep._use_dep(%s)" % repr(self.tokens)

    def evaluate_conditionals(self, use: frozenset) -> _use_dep:
        """
        Create a new instance with conditionals evaluated.

        Conditional evaluation behavior:

                parent state   conditional   result

                 x              x?            x
                -x              x?
                 x             !x?
                -x             !x?           -x

                 x              x=            x
                -x              x=           -x
                 x             !x=           -x
                -x             !x=            x

        Conditional syntax examples:

                Compact Form        Equivalent Expanded Form

                foo[bar?]           bar? ( foo[bar]  ) !bar? ( foo       )
                foo[!bar?]          bar? ( foo       ) !bar? ( foo[-bar] )
                foo[bar=]           bar? ( foo[bar]  ) !bar? ( foo[-bar] )
                foo[!bar=]          bar? ( foo[-bar] ) !bar? ( foo[bar]  )

        """
        enabled_flags = set(self.enabled)
        disabled_flags = set(self.disabled)

        tokens = []
        usedep_re = _get_usedep_re(self._eapi_attrs)

        for x in self.tokens:
            m = usedep_re.match(x)

            operator = m.group("prefix") + m.group("suffix")
            flag = m.group("flag")
            default = m.group("default")
            if default is None:
                default = ""

            if operator == "?":
                if flag in use:
                    enabled_flags.add(flag)
                    tokens.append(flag + default)
            elif operator == "=":
                if flag in use:
                    enabled_flags.add(flag)
                    tokens.append(flag + default)
                else:
                    disabled_flags.add(flag)
                    tokens.append("-" + flag + default)
            elif operator == "!=":
                if flag in use:
                    disabled_flags.add(flag)
                    tokens.append("-" + flag + default)
                else:
                    enabled_flags.add(flag)
                    tokens.append(flag + default)
            elif operator == "!?":
                if flag not in use:
                    disabled_flags.add(flag)
                    tokens.append("-" + flag + default)
            else:
                tokens.append(x)

        return _use_dep(
            tokens,
            self._eapi_attrs,
            enabled_flags=enabled_flags,
            disabled_flags=disabled_flags,
            missing_enabled=self.missing_enabled,
            missing_disabled=self.missing_disabled,
            required=self.required,
        )

    def violated_conditionals(
        self,
        other_use: frozenset,
        is_valid_flag: Callable,
        parent_use: Optional[Any] = None,
    ) -> _use_dep:
        """
        Create a new instance with satisfied use deps removed.
        """
        if parent_use is None and self.conditional:
            raise InvalidAtom(
                "violated_conditionals needs 'parent_use'"
                + " parameter for conditional flags."
            )

        enabled_flags = set()
        disabled_flags = set()

        conditional = {}
        tokens = []

        all_defaults = self.missing_enabled | self.missing_disabled

        def validate_flag(flag: str) -> bool:
            return is_valid_flag(flag) or flag in all_defaults

        usedep_re = _get_usedep_re(self._eapi_attrs)

        for x in self.tokens:
            m = usedep_re.match(x)

            operator = m.group("prefix") + m.group("suffix")
            flag = m.group("flag")

            if not validate_flag(flag):
                tokens.append(x)
                if not operator:
                    enabled_flags.add(flag)
                elif operator == "-":
                    disabled_flags.add(flag)
                elif operator == "?":
                    conditional.setdefault("enabled", set()).add(flag)
                elif operator == "=":
                    conditional.setdefault("equal", set()).add(flag)
                elif operator == "!=":
                    conditional.setdefault("not_equal", set()).add(flag)
                elif operator == "!?":
                    conditional.setdefault("disabled", set()).add(flag)

                continue

            if not operator:
                if flag not in other_use:
                    if is_valid_flag(flag) or flag in self.missing_disabled:
                        tokens.append(x)
                        enabled_flags.add(flag)
            elif operator == "-":
                if flag not in other_use:
                    if not is_valid_flag(flag):
                        if flag in self.missing_enabled:
                            tokens.append(x)
                            disabled_flags.add(flag)
                else:
                    tokens.append(x)
                    disabled_flags.add(flag)
            elif operator == "?":
                if flag not in parent_use or flag in other_use:
                    continue

                if is_valid_flag(flag) or flag in self.missing_disabled:
                    tokens.append(x)
                    conditional.setdefault("enabled", set()).add(flag)
            elif operator == "=":
                if flag in parent_use and flag not in other_use:
                    if is_valid_flag(flag):
                        tokens.append(x)
                        conditional.setdefault("equal", set()).add(flag)
                    else:
                        if flag in self.missing_disabled:
                            tokens.append(x)
                            conditional.setdefault("equal", set()).add(flag)
                elif flag not in parent_use:
                    if flag not in other_use:
                        if not is_valid_flag(flag):
                            if flag in self.missing_enabled:
                                tokens.append(x)
                                conditional.setdefault("equal", set()).add(flag)
                    else:
                        tokens.append(x)
                        conditional.setdefault("equal", set()).add(flag)
            elif operator == "!=":
                if flag not in parent_use and flag not in other_use:
                    if is_valid_flag(flag):
                        tokens.append(x)
                        conditional.setdefault("not_equal", set()).add(flag)
                    else:
                        if flag in self.missing_disabled:
                            tokens.append(x)
                            conditional.setdefault("not_equal", set()).add(flag)
                elif flag in parent_use:
                    if flag not in other_use:
                        if not is_valid_flag(flag):
                            if flag in self.missing_enabled:
                                tokens.append(x)
                                conditional.setdefault("not_equal", set()).add(flag)
                    else:
                        tokens.append(x)
                        conditional.setdefault("not_equal", set()).add(flag)
            elif operator == "!?":
                if flag not in parent_use:
                    if flag not in other_use:
                        if not is_valid_flag(flag) and flag in self.missing_enabled:
                            tokens.append(x)
                            conditional.setdefault("disabled", set()).add(flag)
                    else:
                        tokens.append(x)
                        conditional.setdefault("disabled", set()).add(flag)

        return _use_dep(
            tokens,
            self._eapi_attrs,
            enabled_flags=enabled_flags,
            disabled_flags=disabled_flags,
            missing_enabled=self.missing_enabled,
            missing_disabled=self.missing_disabled,
            conditional=conditional,
            required=self.required,
        )

    def _eval_qa_conditionals(self, use_mask, use_force):
        """
        For repoman, evaluate all possible combinations within the constraints
        of the given use.force and use.mask settings. The result may seem
        ambiguous in the sense that the same flag can be in both the enabled
        and disabled sets, but this is useful within the context of how its
        intended to be used by repoman. It is assumed that the caller has
        already ensured that there is no intersection between the given
        use_mask and use_force sets when necessary.
        """
        enabled_flags = set(self.enabled)
        disabled_flags = set(self.disabled)
        missing_enabled = self.missing_enabled
        missing_disabled = self.missing_disabled

        tokens = []
        usedep_re = _get_usedep_re(self._eapi_attrs)

        for x in self.tokens:
            m = usedep_re.match(x)

            operator = m.group("prefix") + m.group("suffix")
            flag = m.group("flag")
            default = m.group("default")
            if default is None:
                default = ""

            if operator == "?":
                if flag not in use_mask:
                    enabled_flags.add(flag)
                    tokens.append(flag + default)
            elif operator == "=":
                if flag not in use_mask:
                    enabled_flags.add(flag)
                    tokens.append(flag + default)
                if flag not in use_force:
                    disabled_flags.add(flag)
                    tokens.append("-" + flag + default)
            elif operator == "!=":
                if flag not in use_force:
                    enabled_flags.add(flag)
                    tokens.append(flag + default)
                if flag not in use_mask:
                    disabled_flags.add(flag)
                    tokens.append("-" + flag + default)
            elif operator == "!?":
                if flag not in use_force:
                    disabled_flags.add(flag)
                    tokens.append("-" + flag + default)
            else:
                tokens.append(x)

        return _use_dep(
            tokens,
            self._eapi_attrs,
            enabled_flags=enabled_flags,
            disabled_flags=disabled_flags,
            missing_enabled=missing_enabled,
            missing_disabled=missing_disabled,
            required=self.required,
        )


_extended_cp_re_cache = {}


def extended_cp_match(extended_cp: str, other_cp: str) -> bool:
    """
    Checks if an extended syntax cp matches a non extended cp
    """
    # Escape special '+' and '.' characters which are allowed in atoms,
    # and convert '*' to regex equivalent.
    global _extended_cp_re_cache
    extended_cp_re = _extended_cp_re_cache.get(extended_cp)
    if extended_cp_re is None:
        extended_cp_re = re.compile(
            "^" + re.escape(extended_cp).replace(r"\*", "[^/]*") + "$", re.UNICODE
        )
        _extended_cp_re_cache[extended_cp] = extended_cp_re
    return extended_cp_re.match(other_cp) is not None


def get_operator(mydep):
    """
    Return the operator used in a depstring.

    Example usage:
            >>> from portage.dep import *
            >>> get_operator(">=test-1.0")
            '>='

    @param mydep: The dep string to check
    @type mydep: String
    @rtype: String
    @return: The operator. One of:
            '~', '=', '>', '<', '=*', '>=', or '<='
    """
    if not isinstance(mydep, Atom):
        mydep = Atom(mydep)

    return mydep.operator


def dep_getcpv(mydep):
    """
    Return the category-package-version with any operators/slot specifications stripped off

    Example usage:
            >>> dep_getcpv('>=media-libs/test-3.0')
            'media-libs/test-3.0'

    @param mydep: The depstring
    @type mydep: String
    @rtype: String
    @return: The depstring with the operator removed
    """
    if not isinstance(mydep, Atom):
        mydep = Atom(mydep)

    return mydep.cpv


def dep_getslot(mydep: Atom) -> Optional[str]:
    """
    Retrieve the slot on a depend.

    Example usage:
            >>> dep_getslot('app-misc/test:3')
            '3'

    @param mydep: The depstring to retrieve the slot of
    @type mydep: String
    @rtype: String
    @return: The slot
    """
    slot = getattr(mydep, "slot", False)
    if slot is not False:
        return slot

    # remove repo_name if present
    mydep = mydep.split(_repo_separator)[0]

    colon = mydep.find(_slot_separator)
    if colon != -1:
        bracket = mydep.find("[", colon)
        if bracket == -1:
            return mydep[colon + 1 :]
        return mydep[colon + 1 : bracket]
    return None


def dep_getrepo(mydep):
    """
    Retrieve the repo on a depend.

    @param mydep: The depstring to retrieve the repository of
    @type mydep: String
    @rtype: String
    @return: The repository name

    Example usage:
            >>> dep_getrepo('app-misc/test::repository')
            'repository'
    """
    repo = getattr(mydep, "repo", False)
    if repo is not False:
        return repo

    metadata = getattr(mydep, "metadata", False)
    if metadata:
        repo = metadata.get("repository", False)
        if repo is not False:
            return repo

    colon = mydep.find(_repo_separator)
    if colon != -1:
        bracket = mydep.find("[", colon)
        if bracket == -1:
            return mydep[colon + 2 :]
        return mydep[colon + 2 : bracket]
    return None


def remove_slot(mydep: Atom) -> Union[Atom, str]:
    """
    Removes dep components from the right side of an atom:
            - slot
            - use
            - repo
    And repo_name from the left side.

    @type mydep: String
    @rtype: String
    """
    colon = mydep.find(_slot_separator)
    if colon != -1:
        mydep = mydep[:colon]
    else:
        bracket = mydep.find("[")
        if bracket != -1:
            mydep = mydep[:bracket]
    return mydep


def dep_getusedeps(depend):
    """
    Pull a listing of USE Dependencies out of a dep atom.

    @param depend: The depstring to process
    @type depend: String
    @rtype: List
    @return: List of use flags ( or [] if no flags exist )

    Example usage:
            >>> dep_getusedeps('app-misc/test:3[foo,-bar]')
            ('foo', '-bar')
    """
    use_list = []
    open_bracket = depend.find("[")
    # -1 = failure (think c++ string::npos)
    comma_separated = False
    bracket_count = 0
    while open_bracket != -1:
        bracket_count += 1
        if bracket_count > 1:
            raise InvalidAtom(
                _("USE Dependency with more " "than one set of brackets: %s")
                % (depend,)
            )
        close_bracket = depend.find("]", open_bracket)
        if close_bracket == -1:
            raise InvalidAtom(_("USE Dependency with no closing bracket: %s") % depend)
        use = depend[open_bracket + 1 : close_bracket]
        # foo[1:1] may return '' instead of None, we don't want '' in the result
        if not use:
            raise InvalidAtom(_("USE Dependency with " "no use flag ([]): %s") % depend)
        if not comma_separated:
            comma_separated = "," in use

        if comma_separated and bracket_count > 1:
            raise InvalidAtom(
                _(
                    "USE Dependency contains a mixture of "
                    "comma and bracket separators: %s"
                )
                % depend
            )

        if comma_separated:
            for x in use.split(","):
                if x:
                    use_list.append(x)
                else:
                    raise InvalidAtom(
                        _("USE Dependency with no use " "flag next to comma: %s")
                        % depend
                    )
        else:
            use_list.append(use)

        # Find next use flag
        open_bracket = depend.find("[", open_bracket + 1)
    return tuple(use_list)


def isvalidatom(
    atom: str,
    allow_blockers: bool = False,
    allow_wildcard: bool = False,
    allow_repo: bool = False,
    eapi: Optional[Any] = None,
    allow_build_id: bool = False,
) -> bool:
    """
    Check to see if a depend atom is valid

    Example usage:
            >>> isvalidatom('media-libs/test-3.0')
            False
            >>> isvalidatom('>=media-libs/test-3.0')
            True

    @param atom: The depend atom to check against
    @type atom: String or Atom
    @rtype: Boolean
    @return: One of the following:
            1) False if the atom is invalid
            2) True if the atom is valid
    """

    if eapi is not None and isinstance(atom, Atom) and atom.eapi != eapi:
        # We'll construct a new atom with the given eapi.
        atom = str(atom)

    try:
        if not isinstance(atom, Atom):
            atom = Atom(
                atom,
                allow_wildcard=allow_wildcard,
                allow_repo=allow_repo,
                eapi=eapi,
                allow_build_id=allow_build_id,
            )
        if not allow_blockers and atom.blocker:
            return False
        return True
    except InvalidAtom:
        return False


def isjustname(mypkg):
    """
    Checks to see if the atom is only the package name (no version parts).

    Example usage:
            >>> isjustname('=media-libs/test-3.0')
            False
            >>> isjustname('media-libs/test')
            True

    @param mypkg: The package atom to check
    @param mypkg: String or Atom
    @rtype: Integer
    @return: One of the following:
            1) False if the package string is not just the package name
            2) True if it is
    """
    try:
        if not isinstance(mypkg, Atom):
            mypkg = Atom(mypkg)
        return mypkg == mypkg.cp
    except InvalidAtom:
        pass

    for x in mypkg.split("-")[-2:]:
        if ververify(x):
            return False
    return True


def isspecific(mypkg):
    """
    Checks to see if a package is in =category/package-version or
    package-version format.

    Example usage:
            >>> isspecific('media-libs/test')
            False
            >>> isspecific('=media-libs/test-3.0')
            True

    @param mypkg: The package depstring to check against
    @type mypkg: String
    @rtype: Boolean
    @return: One of the following:
            1) False if the package string is not specific
            2) True if it is
    """
    try:
        if not isinstance(mypkg, Atom):
            mypkg = Atom(mypkg)
        return mypkg != mypkg.cp
    except InvalidAtom:
        pass

    # Fall back to legacy code for backward compatibility.
    return not isjustname(mypkg)


def dep_getkey(mydep):
    """
    Return the category/package-name of a depstring.

    Example usage:
            >>> dep_getkey('=media-libs/test-3.0')
            'media-libs/test'

    @param mydep: The depstring to retrieve the category/package-name of
    @type mydep: String
    @rtype: String
    @return: The package category/package-name
    """
    if not isinstance(mydep, Atom):
        mydep = Atom(mydep, allow_wildcard=True, allow_repo=True)

    return mydep.cp


def match_to_list(
    mypkg: Union[Package, _pkg_str], mylist: Union[List[Atom], Iterator]
) -> List[Atom]:
    """
    Searches list for entries that matches the package.

    @param mypkg: The package atom to match
    @type mypkg: String
    @param mylist: The list of package atoms to compare against
    @type mylist: List
    @rtype: List
    @return: A unique list of package atoms that match the given package atom
    """
    matches = set()
    matches_add = matches.add
    pkgs = (mypkg,)
    result = [
        x
        for x in mylist
        if not (x in matches or matches_add(x)) and match_from_list(x, pkgs)
    ]
    return result


def best_match_to_list(
    mypkg: Union[Package, _pkg_str], mylist: Union[List[Atom], Iterator]
) -> Optional[Atom]:
    """
    Returns the most specific entry that matches the package given.

    @param mypkg: The package atom to check
    @type mypkg: String
    @param mylist: The list of package atoms to check against
    @type mylist: List
    @rtype: String
    @return: The package atom which best matches given the following ordering:
            - =cpv      6
            - ~cpv      5
            - =cpv*     4
            - cp:slot   3
            - >cpv      2
            - <cpv      2
            - >=cpv     2
            - <=cpv     2
            - cp        1
            - cp:slot with extended syntax	0
            - cp with extended syntax	-1
    """
    operator_values = {
        "=": 6,
        "~": 5,
        "=*": 4,
        ">": 2,
        "<": 2,
        ">=": 2,
        "<=": 2,
        None: 1,
    }
    maxvalue = -99
    bestm = None
    mypkg_cpv = None
    for x in match_to_list(mypkg, mylist):
        if x.extended_syntax:
            if x.operator == "=*":
                if maxvalue < 0:
                    maxvalue = 0
                    bestm = x
            elif x.slot is not None:
                if maxvalue < -1:
                    maxvalue = -1
                    bestm = x
            else:
                if maxvalue < -2:
                    maxvalue = -2
                    bestm = x
            continue
        if dep_getslot(x) is not None:
            if maxvalue < 3:
                maxvalue = 3
                bestm = x
        op_val = operator_values[x.operator]
        if op_val > maxvalue:
            maxvalue = op_val
            bestm = x
        elif op_val == maxvalue and op_val == 2:
            # For >, <, >=, and <=, the one with the version
            # closest to mypkg is the best match.
            if mypkg_cpv is None:
                try:
                    mypkg_cpv = mypkg.cpv
                except AttributeError:
                    mypkg_cpv = _pkg_str(remove_slot(mypkg))
            if bestm.cpv == mypkg_cpv or bestm.cpv == x.cpv:
                pass
            elif x.cpv == mypkg_cpv:
                bestm = x
            else:
                # Sort the cpvs to find the one closest to mypkg_cpv
                cpv_list = [bestm.cpv, mypkg_cpv, x.cpv]

                def cmp_cpv(cpv1: _pkg_str, cpv2: _pkg_str) -> int:
                    return vercmp(cpv1.version, cpv2.version)

                cpv_list.sort(key=cmp_sort_key(cmp_cpv))
                if cpv_list[0] is mypkg_cpv or cpv_list[-1] is mypkg_cpv:
                    if cpv_list[1] is x.cpv:
                        bestm = x
                else:
                    # TODO: handle the case where mypkg_cpv is in the middle
                    pass

    return bestm


def match_from_list(
    mydep: Atom,
    candidate_list: Union[
        List[Package], List[_pkg_str], Tuple[Union[Package, _pkg_str]]
    ],
) -> Union[List[Package], List[_pkg_str]]:
    """
    Searches list for entries that matches the package.

    @param mydep: The package atom to match
    @type mydep: String
    @param candidate_list: The list of package atoms to compare against
    @type candidate_list: List
    @rtype: List
    @return: A list of package atoms that match the given package atom
    """

    if not candidate_list:
        return []

    if "!" == mydep[:1]:
        if "!" == mydep[1:2]:
            mydep = mydep[2:]
        else:
            mydep = mydep[1:]
    if not isinstance(mydep, Atom):
        mydep = Atom(mydep, allow_wildcard=True, allow_repo=True)

    mycpv = mydep.cpv
    mycpv_cps = catpkgsplit(mycpv)  # Can be None if not specific
    build_id = mydep.build_id

    if not mycpv_cps:
        ver = None
        rev = None
    else:
        cat, pkg, ver, rev = mycpv_cps
        if mydep == mycpv:
            raise KeyError(
                _("Specific key requires an operator" " (%s) (try adding an '=')")
                % (mydep)
            )

    if ver and rev:
        operator = mydep.operator
        if not operator:
            writemsg(_("!!! Invalid atom: %s\n") % mydep, noiselevel=-1)
            return []
    else:
        operator = None

    mylist = []

    if mydep.extended_syntax:

        for x in candidate_list:
            cp = getattr(x, "cp", None)
            if cp is None:
                mysplit = catpkgsplit(remove_slot(x))
                if mysplit is not None:
                    cp = mysplit[0] + "/" + mysplit[1]

            if cp is None:
                continue

            if cp == mycpv or extended_cp_match(mydep.cp, cp):
                mylist.append(x)

        if mylist and mydep.operator == "=*":

            candidate_list = mylist
            mylist = []
            # Currently, only \*\w+\* is supported.
            ver = mydep.version[1:-1]

            for x in candidate_list:
                x_ver = getattr(x, "version", None)
                if x_ver is None:
                    xs = catpkgsplit(remove_slot(x))
                    if xs is None:
                        continue
                    x_ver = "-".join(xs[-2:])
                if ver in x_ver:
                    mylist.append(x)

    elif operator is None:
        for x in candidate_list:
            cp = getattr(x, "cp", None)
            if cp is None:
                mysplit = catpkgsplit(remove_slot(x))
                if mysplit is not None:
                    cp = mysplit[0] + "/" + mysplit[1]

            if cp is None:
                continue

            if cp == mydep.cp:
                mylist.append(x)

    elif operator == "=":  # Exact match
        for x in candidate_list:
            xcpv = getattr(x, "cpv", None)
            if xcpv is None:
                xcpv = remove_slot(x)
            if not cpvequal(xcpv, mycpv):
                continue
            if build_id is not None and getattr(xcpv, "build_id", None) != build_id:
                continue
            mylist.append(x)

    elif operator == "=*":  # glob match
        # XXX: Nasty special casing for leading zeros
        # Required as =* is a literal prefix match, so can't
        # use vercmp
        myver = mycpv_cps[2].lstrip("0")
        if not myver or not myver[0].isdigit():
            myver = "0" + myver
        if myver == mycpv_cps[2]:
            mycpv_cmp = mycpv
        else:
            # Use replace to preserve the revision part if it exists
            # (mycpv_cps[3] can't be trusted because in contains r0
            # even when the input has no revision part).
            mycpv_cmp = mycpv.replace(
                mydep.cp + "-" + mycpv_cps[2], mydep.cp + "-" + myver, 1
            )
        for x in candidate_list:
            try:
                x.cp
            except AttributeError:
                try:
                    pkg = _pkg_str(remove_slot(x))
                except InvalidData:
                    continue
            else:
                pkg = x

            xs = pkg.cpv_split
            myver = xs[2].lstrip("0")
            if not myver or not myver[0].isdigit():
                myver = "0" + myver
            if myver == xs[2]:
                xcpv = pkg.cpv
            else:
                # Use replace to preserve the revision part if it exists.
                xcpv = pkg.cpv.replace(pkg.cp + "-" + xs[2], pkg.cp + "-" + myver, 1)
            if xcpv.startswith(mycpv_cmp):
                # =* glob matches only on boundaries between version parts,
                # so 1* does not match 10 (bug 560466).
                next_char = xcpv[len(mycpv_cmp) : len(mycpv_cmp) + 1]
                if (
                    not next_char
                    or next_char in "._-"
                    or mycpv_cmp[-1].isdigit() != next_char.isdigit()
                ):
                    mylist.append(x)

    elif operator == "~":  # version, any revision, match
        for x in candidate_list:
            xs = getattr(x, "cpv_split", None)
            if xs is None:
                xs = catpkgsplit(remove_slot(x))
            if xs is None:
                raise InvalidData(x)
            if not cpvequal(
                xs[0] + "/" + xs[1] + "-" + xs[2],
                mycpv_cps[0] + "/" + mycpv_cps[1] + "-" + mycpv_cps[2],
            ):
                continue
            if xs[2] != ver:
                continue
            mylist.append(x)

    elif operator in (">", ">=", "<", "<="):
        for x in candidate_list:
            if hasattr(x, "cp"):
                pkg = x
            else:
                try:
                    pkg = _pkg_str(remove_slot(x))
                except InvalidData:
                    continue

            if pkg.cp != mydep.cp:
                continue
            try:
                result = vercmp(pkg.version, mydep.version)
            except ValueError:  # pkgcmp may return ValueError during int() conversion
                writemsg(_("\nInvalid package name: %s\n") % x, noiselevel=-1)
                raise
            if result is None:
                continue
            elif operator == ">":
                if result > 0:
                    mylist.append(x)
            elif operator == ">=":
                if result >= 0:
                    mylist.append(x)
            elif operator == "<":
                if result < 0:
                    mylist.append(x)
            elif operator == "<=":
                if result <= 0:
                    mylist.append(x)
            else:
                raise KeyError(_("Unknown operator: %s") % mydep)
    else:
        raise KeyError(_("Unknown operator: %s") % mydep)

    if mydep.slot is not None:
        candidate_list = mylist
        mylist = []
        for x in candidate_list:
            x_pkg = None
            try:
                x.cpv
            except AttributeError:
                xslot = dep_getslot(x)
                if xslot is not None:
                    try:
                        x_pkg = _pkg_str(remove_slot(x), slot=xslot)
                    except InvalidData:
                        continue
            else:
                x_pkg = x

            if x_pkg is None:
                mylist.append(x)
            else:
                try:
                    x_pkg.slot
                except AttributeError:
                    mylist.append(x)
                else:
                    if _match_slot(mydep, x_pkg):
                        mylist.append(x)

    if mydep.unevaluated_atom.use:
        candidate_list = mylist
        mylist = []
        for x in candidate_list:
            use = getattr(x, "use", None)
            if use is not None:
                if mydep.unevaluated_atom.use and not x.iuse.is_valid_flag(
                    mydep.unevaluated_atom.use.required
                ):
                    continue

                if mydep.use:
                    is_valid_flag = x.iuse.is_valid_flag
                    missing_enabled = frozenset(
                        flag
                        for flag in mydep.use.missing_enabled
                        if not is_valid_flag(flag)
                    )
                    missing_disabled = frozenset(
                        flag
                        for flag in mydep.use.missing_disabled
                        if not is_valid_flag(flag)
                    )

                    if mydep.use.enabled:
                        if any(f in mydep.use.enabled for f in missing_disabled):
                            continue
                        need_enabled = mydep.use.enabled - use.enabled
                        if need_enabled:
                            if any(f not in missing_enabled for f in need_enabled):
                                continue

                    if mydep.use.disabled:
                        if any(f in mydep.use.disabled for f in missing_enabled):
                            continue
                        need_disabled = mydep.use.disabled & use.enabled
                        if need_disabled:
                            if any(f not in missing_disabled for f in need_disabled):
                                continue

            mylist.append(x)

    if mydep.repo:
        candidate_list = mylist
        mylist = []
        for x in candidate_list:
            repo = getattr(x, "repo", False)
            if repo is False:
                repo = dep_getrepo(x)
            if repo is not None and repo != _unknown_repo and repo != mydep.repo:
                continue
            mylist.append(x)

    return mylist


def human_readable_required_use(required_use):
    return (
        required_use.replace("^^", "exactly-one-of")
        .replace("||", "any-of")
        .replace("??", "at-most-one-of")
    )


def get_required_use_flags(required_use, eapi=None):
    """
    Returns a set of use flags that are used in the given REQUIRED_USE string

    @param required_use: REQUIRED_USE string
    @type required_use: String
    @rtype: Set
    @return: Set of use flags that are used in the given REQUIRED_USE string
    """

    eapi_attrs = _get_eapi_attrs(eapi)
    if eapi_attrs.required_use_at_most_one_of:
        valid_operators = ("||", "^^", "??")
    else:
        valid_operators = ("||", "^^")

    mysplit = required_use.split()
    level = 0
    stack = [[]]
    need_bracket = False

    used_flags = set()

    def register_token(token):
        if token.endswith("?"):
            token = token[:-1]
        if token.startswith("!"):
            token = token[1:]
        used_flags.add(token)

    for token in mysplit:
        if token == "(":
            need_bracket = False
            stack.append([])
            level += 1
        elif token == ")":
            if need_bracket:
                raise InvalidDependString(_("malformed syntax: '%s'") % required_use)
            if level > 0:
                level -= 1
                l = stack.pop()
                ignore = False
                if stack[level]:
                    if stack[level][-1] in valid_operators or (
                        not isinstance(stack[level][-1], bool)
                        and stack[level][-1][-1] == "?"
                    ):
                        ignore = True
                        stack[level].pop()
                        stack[level].append(True)

                if l and not ignore:
                    stack[level].append(all(x for x in l))
            else:
                raise InvalidDependString(_("malformed syntax: '%s'") % required_use)
        elif token in valid_operators:
            if need_bracket:
                raise InvalidDependString(_("malformed syntax: '%s'") % required_use)
            need_bracket = True
            stack[level].append(token)
        else:
            if need_bracket:
                raise InvalidDependString(_("malformed syntax: '%s'") % required_use)

            if token[-1] == "?":
                need_bracket = True
                stack[level].append(token)
            else:
                stack[level].append(True)

            register_token(token)

    if level != 0 or need_bracket:
        raise InvalidDependString(_("malformed syntax: '%s'") % required_use)

    return frozenset(used_flags)


class _RequiredUseLeaf:

    __slots__ = ("_satisfied", "_token")

    def __init__(self, token: str, satisfied: bool) -> None:
        self._token = token
        self._satisfied = satisfied

    def tounicode(self):
        return self._token


class _RequiredUseBranch:

    __slots__ = ("_children", "_operator", "_parent", "_satisfied")

    def __init__(
        self,
        operator: Optional[str] = None,
        parent: Optional[_RequiredUseBranch] = None,
    ) -> None:
        self._children = []
        self._operator = operator
        self._parent = parent
        self._satisfied = False

    def __bool__(self) -> bool:
        return self._satisfied

    def tounicode(self):

        include_parens = self._parent is not None
        tokens = []
        if self._operator is not None:
            tokens.append(self._operator)

        if include_parens:
            tokens.append("(")

        complex_nesting = False
        node = self
        while node is not None and not complex_nesting:
            if node._operator in ("||", "^^", "??"):
                complex_nesting = True
            else:
                node = node._parent

        if complex_nesting:
            for child in self._children:
                tokens.append(child.tounicode())
        else:
            for child in self._children:
                if not child._satisfied:
                    tokens.append(child.tounicode())

        if include_parens:
            tokens.append(")")

        return " ".join(tokens)


def check_required_use(
    required_use: str, use: Tuple[()], iuse_match: Callable, eapi: str = None
) -> _RequiredUseBranch:
    """
    Checks if the use flags listed in 'use' satisfy all
    constraints specified in 'required_use'.

    @param required_use: REQUIRED_USE string
    @type required_use: String
    @param use: Enabled use flags
    @type use: List
    @param iuse_match: Callable that takes a single flag argument and returns
            True if the flag is matched, false otherwise,
    @type iuse_match: Callable
    @rtype: Bool
    @return: Indicates if REQUIRED_USE constraints are satisfied
    """

    eapi_attrs = _get_eapi_attrs(eapi)
    if eapi_attrs.required_use_at_most_one_of:
        valid_operators = ("||", "^^", "??")
    else:
        valid_operators = ("||", "^^")

    def is_active(token: str) -> bool:
        if token.startswith("!"):
            flag = token[1:]
            is_negated = True
        else:
            flag = token
            is_negated = False

        if not flag or not iuse_match(flag):
            if not eapi_attrs.required_use_at_most_one_of and flag == "?":
                msg = _("Operator '??' is not supported with EAPI '%s'") % (eapi,)
                e = InvalidData(msg, category="EAPI.incompatible")
                raise InvalidDependString(msg, errors=(e,))
            msg = _("USE flag '%s' is not in IUSE") % (flag,)
            e = InvalidData(msg, category="IUSE.missing")
            raise InvalidDependString(msg, errors=(e,))

        return (flag in use and not is_negated) or (flag not in use and is_negated)

    def is_satisfied(operator: str, argument: List[bool]) -> bool:
        if not argument and eapi_attrs.empty_groups_always_true:
            # || ( ) -> True
            return True

        if operator == "||":
            return True in argument
        if operator == "^^":
            return argument.count(True) == 1
        if operator == "??":
            return argument.count(True) <= 1
        if operator[-1] == "?":
            return False not in argument

    mysplit = required_use.split()
    level = 0
    stack = [[]]
    tree = _RequiredUseBranch()
    node = tree
    need_bracket = False

    for token in mysplit:
        if token == "(":
            if not need_bracket:
                child = _RequiredUseBranch(parent=node)
                node._children.append(child)
                node = child

            need_bracket = False
            stack.append([])
            level += 1
        elif token == ")":
            if need_bracket:
                raise InvalidDependString(_("malformed syntax: '%s'") % required_use)
            if level > 0:
                level -= 1
                l = stack.pop()
                op = None
                if stack[level]:
                    if stack[level][-1] in valid_operators:
                        op = stack[level].pop()
                        satisfied = is_satisfied(op, l)
                        stack[level].append(satisfied)
                        node._satisfied = satisfied

                    elif (
                        not isinstance(stack[level][-1], bool)
                        and stack[level][-1][-1] == "?"
                    ):
                        op = stack[level].pop()
                        if is_active(op[:-1]):
                            satisfied = is_satisfied(op, l)
                            stack[level].append(satisfied)
                            node._satisfied = satisfied
                        else:
                            node._satisfied = True
                            last_node = node._parent._children.pop()
                            if last_node is not node:
                                raise AssertionError("node is not last child of parent")
                            node = node._parent
                            continue

                if op is None:
                    satisfied = False not in l
                    node._satisfied = satisfied
                    if l:
                        stack[level].append(satisfied)

                    if (
                        len(node._children) <= 1
                        or node._parent._operator not in valid_operators
                    ):
                        last_node = node._parent._children.pop()
                        if last_node is not node:
                            raise AssertionError("node is not last child of parent")
                        for child in node._children:
                            node._parent._children.append(child)
                            if isinstance(child, _RequiredUseBranch):
                                child._parent = node._parent

                elif not node._children:
                    last_node = node._parent._children.pop()
                    if last_node is not node:
                        raise AssertionError("node is not last child of parent")

                elif len(node._children) == 1 and op in valid_operators:
                    last_node = node._parent._children.pop()
                    if last_node is not node:
                        raise AssertionError("node is not last child of parent")
                    node._parent._children.append(node._children[0])
                    if isinstance(node._children[0], _RequiredUseBranch):
                        node._children[0]._parent = node._parent
                        node = node._children[0]
                        if (
                            node._operator is None
                            and node._parent._operator not in valid_operators
                        ):
                            last_node = node._parent._children.pop()
                            if last_node is not node:
                                raise AssertionError("node is not last child of parent")
                            for child in node._children:
                                node._parent._children.append(child)
                                if isinstance(child, _RequiredUseBranch):
                                    child._parent = node._parent

                node = node._parent
            else:
                raise InvalidDependString(_("malformed syntax: '%s'") % required_use)
        elif token in valid_operators:
            if need_bracket:
                raise InvalidDependString(_("malformed syntax: '%s'") % required_use)
            need_bracket = True
            stack[level].append(token)
            child = _RequiredUseBranch(operator=token, parent=node)
            node._children.append(child)
            node = child
        else:
            if need_bracket:
                raise InvalidDependString(_("malformed syntax: '%s'") % required_use)

            if token[-1] == "?":
                need_bracket = True
                stack[level].append(token)
                child = _RequiredUseBranch(operator=token, parent=node)
                node._children.append(child)
                node = child
            else:
                satisfied = is_active(token)
                stack[level].append(satisfied)
                node._children.append(_RequiredUseLeaf(token, satisfied))

    if level != 0 or need_bracket:
        raise InvalidDependString(_("malformed syntax: '%s'") % required_use)

    tree._satisfied = False not in stack[0]
    return tree


def extract_affecting_use(mystr, atom, eapi=None):
    """
    Take a dep string and an atom and return the use flags
    that decide if the given atom is in effect.

    @param mystr: The dependency string
    @type mystr: String
    @param atom: The atom to get into effect
    @type atom: String
    @rtype: Set of strings
    @return: Set of use flags affecting given atom

    Example usage:
            >>> extract_affecting_use('sasl? ( dev-libs/cyrus-sasl ) \
                    !minimal? ( cxx? ( dev-libs/cyrus-sasl ) )', 'dev-libs/cyrus-sasl')
            {'cxx', 'minimal', 'sasl'}
    """
    useflag_re = _get_useflag_re(eapi)
    mysplit = mystr.split()
    level = 0
    stack = [[]]
    need_bracket = False
    affecting_use = set()

    def flag(conditional):
        if conditional[0] == "!":
            flag = conditional[1:-1]
        else:
            flag = conditional[:-1]

        if useflag_re.match(flag) is None:
            raise InvalidDependString(
                _("invalid use flag '%s' in conditional '%s'") % (flag, conditional)
            )

        return flag

    for token in mysplit:
        if token == "(":
            need_bracket = False
            stack.append([])
            level += 1
        elif token == ")":
            if need_bracket:
                raise InvalidDependString(_("malformed syntax: '%s'") % mystr)
            if level > 0:
                level -= 1
                l = stack.pop()
                is_single = len(l) == 1 or (
                    len(l) == 2 and (l[0] == "||" or l[0][-1] == "?")
                )

                def ends_in_any_of_dep(k):
                    return k >= 0 and stack[k] and stack[k][-1] == "||"

                def ends_in_operator(k):
                    return (
                        k >= 0
                        and stack[k]
                        and (stack[k][-1] == "||" or stack[k][-1][-1] == "?")
                    )

                def special_append():
                    """
                    Use extend instead of append if possible. This kills all redundant brackets.
                    """
                    if is_single and (
                        not stack[level] or not stack[level][-1][-1] == "?"
                    ):
                        if len(l) == 1 and isinstance(l[0], list):
                            # l = [[...]]
                            stack[level].extend(l[0])
                        else:
                            stack[level].extend(l)
                    else:
                        stack[level].append(l)

                if l:
                    if not ends_in_any_of_dep(level - 1) and not ends_in_operator(
                        level
                    ):
                        # Optimize: ( ( ... ) ) -> ( ... ). Make sure there is no '||' hanging around.
                        stack[level].extend(l)
                    elif not stack[level]:
                        # An '||' in the level above forces us to keep to brackets.
                        special_append()
                    elif len(l) == 1 and ends_in_any_of_dep(level):
                        # Optimize: || ( A ) -> A
                        stack[level].pop()
                        special_append()
                    elif (
                        len(l) == 2
                        and (l[0] == "||" or l[0][-1] == "?")
                        and stack[level][-1] in (l[0], "||")
                    ):
                        # Optimize: 	|| ( || ( ... ) ) -> || ( ... )
                        # 			foo? ( foo? ( ... ) ) -> foo? ( ... )
                        # 			|| ( foo? ( ... ) ) -> foo? ( ... )
                        stack[level].pop()
                        special_append()
                        if l[0][-1] == "?":
                            affecting_use.add(flag(l[0]))
                    else:
                        if stack[level] and stack[level][-1][-1] == "?":
                            affecting_use.add(flag(stack[level][-1]))
                        special_append()
                else:
                    if stack[level] and (
                        stack[level][-1] == "||" or stack[level][-1][-1] == "?"
                    ):
                        stack[level].pop()
            else:
                raise InvalidDependString(_("malformed syntax: '%s'") % mystr)
        elif token == "||":
            if need_bracket:
                raise InvalidDependString(_("malformed syntax: '%s'") % mystr)
            need_bracket = True
            stack[level].append(token)
        else:
            if need_bracket:
                raise InvalidDependString(_("malformed syntax: '%s'") % mystr)

            if token[-1] == "?":
                need_bracket = True
                stack[level].append(token)
            elif token == atom:
                stack[level].append(token)

    if level != 0 or need_bracket:
        raise InvalidDependString(_("malformed syntax: '%s'") % mystr)

    return affecting_use
