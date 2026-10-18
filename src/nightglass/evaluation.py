"""Review-budget evaluation. See README section 9.

The primary metric is recall of unique known red-team keys at fixed daily
review budgets. Unlabeled rows are not treated as verified benign.
"""


def recall_at_daily_budget(scores, keys, budget):
    """Fraction of unique red-team keys with at least one matching row inside
    each day's top-`budget` scored authentication records."""
    raise NotImplementedError
