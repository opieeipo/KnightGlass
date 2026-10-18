"""Causal feature state. See README section 6.

Events at time t see only observations with timestamps strictly less than t.
Equal-timestamp records are scored as a batch before state is updated.
"""


def build_auth_features(auth_dir, windows_seconds):
    """Relationship novelty, recency, volume, failure-rate, and type-frequency
    features from authentication history."""
    raise NotImplementedError


def build_dns_features(auth_dir, dns_dir, windows_seconds):
    """Source/destination DNS context features. Never-observed history is
    represented separately from a measured elapsed time."""
    raise NotImplementedError
