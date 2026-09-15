"""SCRUM-32 - run the data-correctness invariants in CI, against committed data.

CI has no lake, so the invariant suite only ever ran on a developer's machine when someone
remembered. A change that broke de-cumulation went green and stayed green - the ticket's
own note says this is the difference between finding it on day 3 and day 9.

The `invariant` marker already existed in pyproject and nothing carried it, so
`pytest -m invariant` selected zero tests and passed. A green no-op is worse than no
check, because it is indistinguishable from a real one on the status page.

These run the same three scripts the gold build gates on, against a 1.8 MB slice of real
bronze committed under tests/fixtures/invariants. Real published data, not synthetic: an
invariant that holds on numbers we invented proves nothing about the numbers we serve.

Each check is given an explicit floor for how much it must compare. That is not belt and
braces - two of the three used to report OK when they compared *nothing*, which is exactly
what a scope-collapsing bug produces. See the last test in this file.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "invariants" / "bronze"
SCRIPTS = ROOT / "scripts"

# What the fixture is known to cover. Floors rather than equalities: regenerating the
# fixture from fresher bronze may add periods, and the check that matters is that the
# coverage never silently collapses towards zero.
MIN_PARTITION_COMPARISONS = 3_000  # observed 3,489
MIN_SERIES_YEARS = 400  # observed 467
MIN_PROVINCE_QUARTERS = 150  # observed 162

needs_fixture = pytest.mark.skipif(not FIXTURE.exists(), reason="invariant fixture not committed")


def run(script: str, *arguments: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *arguments],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


@pytest.fixture(scope="module")
def catalog(tmp_path_factory) -> Path:
    """Build a catalog from the fixture bronze, the same way the real one is built."""
    out = tmp_path_factory.mktemp("invariants") / "fixture.duckdb"
    built = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "build_catalog.py"),
            "--bronze",
            str(FIXTURE),
            "--silver",
            str(ROOT / "does-not-exist"),
            "--out",
            str(out),
            "--no-check",  # the checks are the tests below; running them twice proves less
        ],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )
    assert built.returncode == 0, built.stdout + built.stderr
    return out


@pytest.mark.invariant
@needs_fixture
class TestTheInvariantSuite:
    def test_de_cumulation_closes_against_the_published_year(self, catalog):
        """I1: the de-cumulated months of a year must add back to that year's published
        December figure. A wrong de-cumulation is invisible - the numbers stay plausible
        and are simply the wrong size."""
        done = run(
            "check_decumulation.py",
            "--db",
            str(catalog),
            "--min-series-years",
            str(MIN_SERIES_YEARS),
        )
        assert done.returncode == 0, done.stdout + done.stderr

    def test_every_bank_group_partition_closes(self):
        """I6: BDDK's ten taraf scopes form three partitions of the sector, and each must
        sum back to its parent. Needs no second source - the publisher's own arithmetic
        has to hold."""
        done = run(
            "check_taraf_partitions.py",
            "--bronze",
            str(FIXTURE / "aylik"),
            "--min-comparisons",
            str(MIN_PARTITION_COMPARISONS),
        )
        assert done.returncode == 0, done.stdout + done.stderr

    def test_finturk_table_six_reconstructs_table_one(self):
        """I4: table 6's per-capita columns rebuild table 1's published loans. If those
        columns were Bin TL rather than TL the reconstruction would miss by 1000x."""
        done = run(
            "check_finturk_units.py",
            "--bronze",
            str(FIXTURE / "finturk"),
            "--tolerance",
            "0.5",
            "--min-provinces",
            str(MIN_PROVINCE_QUARTERS),
        )
        assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.invariant
@needs_fixture
def test_a_check_that_compares_nothing_is_a_failure_not_a_pass():
    """The failure mode that made this suite worth wiring up.

    A bug collapsing the ten bank-group scopes into one leaves every partition with a
    missing child. Each is skipped, none breaches, and the check reported OK for zero
    comparisons - so the one bug it was written to catch (SCRUM-98) would have passed it.

    Asserted by demanding more comparisons than the fixture can supply, which reaches the
    same guard a collapse would.
    """
    done = run(
        "check_taraf_partitions.py",
        "--bronze",
        str(FIXTURE / "aylik"),
        "--min-comparisons",
        "10000000",
    )
    assert done.returncode == 1
    assert "nothing to check rather than nothing wrong" in done.stdout
