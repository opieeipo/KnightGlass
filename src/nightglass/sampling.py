"""Training-sample construction. See README section 5.4.

Sampling happens after features are computed. Every sample saves its row IDs,
inclusion probabilities, seed, and counts.
"""


def time_stratified_reservoir(feature_dir, cap, seed):
    """Reservoir-sample unlabeled training rows with per-day quotas
    proportional to eligible event counts, keeping all known positives."""
    raise NotImplementedError
