"""Controlled DNS-loss experiments. See README section 7.

DNS records are removed before feature generation and feature state is
replayed. Outage schedules are experiment metadata, never model inputs.
"""


def make_schedule(scenario, duration_seconds, seed):
    """Build a seeded outage schedule chosen independently of labels."""
    raise NotImplementedError


def suppress_dns(dns_dir, schedule, out_dir):
    """Write a DNS partition with the scheduled records removed."""
    raise NotImplementedError
