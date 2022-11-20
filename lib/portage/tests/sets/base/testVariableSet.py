# Copyright 2022 Gentoo Authors
# Distributed under the terms of the GNU General Public License v2

from portage.tests import TestCase
from portage.tests.resolver.ResolverPlayground import (
    ResolverPlayground,
    ResolverPlaygroundTestCase,
)


class VariableSetTestCase(TestCase):
    def testVariableSetEmerge(self):
        ebuilds = {
            "dev-go/go-pkg-1": {"BDEPEND": "dev-lang/go"},
            "dev-go/eclass-go-pkg-1": {
                "MISC_CONTENT": "inherit go-eclass",
            },
        }

        eclasses = {
            "go-eclass": 'BDEPEND="dev-lang/go"'
        }

        installed = ebuilds
        playground = ResolverPlayground(ebuilds=ebuilds,
                                        eclasses=eclasses,
                                        installed=installed,
                                        debug=True)

        test_cases = (
            ResolverPlaygroundTestCase(
                ["@golang-rebuild"],
                mergelist=["dev-go/go-pkg-1", "dev-go/eclass-go-pkg-1"],
                success=True,
            ),
        )

        try:
            for test_case in test_cases:
                # Create an artificial VariableSet to test against
                playground.run_TestCase(test_case)
                #import pdb; pdb.set_trace()
                self.assertEqual(test_case.test_success, True, test_case.fail_msg)
        finally:
            playground.cleanup()
