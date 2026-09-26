"""Constraints, append-only enforcement and query budgets.

Two claims are never made by assertion in this layer. That a constraint exists - it is read
back from `pg_constraint`, because a `CheckConstraint` that never reached a migration is a
comment. And that a privilege is absent - it is proven by connecting as the restricted role and
being refused, because a `REVOKE` in an unapplied migration looks identical in source to one
that was applied ( 1, 5.5).
"""
