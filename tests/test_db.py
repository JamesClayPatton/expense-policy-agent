"""One connection per review: anything that takes an optional connection uses
the shared one when a review has set it, and opens its own otherwise."""
from expense_agent.db import connect, run, sharing


def test_run_prefers_an_explicit_connection_then_the_shared_one_then_its_own():
    with connect() as a, connect() as b:
        with sharing(a):
            assert run(None, lambda c: c) is a
            assert run(b, lambda c: c) is b
        own = run(None, lambda c: c)
        assert own is not a and own is not b and own.closed
