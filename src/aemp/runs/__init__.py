"""Run management: result envelopes, run directories, SQLite index, history.

Chain (M1)::

    Run -> aemp.result/v0 envelope -> runs/<run_id>/ tree -> SQLite index
        -> ``aemp history``

The filesystem is the source of truth; the SQLite index is only a query
cache and can be rebuilt from the run directories at any time.
"""
