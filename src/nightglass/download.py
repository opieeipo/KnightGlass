"""Fetch the LANL archives from Google Drive with gdown.

Each file must be shared as "Anyone with the link". Put its Drive file ID in
configs/experiment.yaml under data.drive_ids. Files that already exist
locally are not downloaded again.
"""

from pathlib import Path


def fetch_raw(config, root="."):
    """Download every configured archive into the raw data directory and
    return the local paths."""
    import gdown

    data = config["data"]
    raw_dir = Path(root) / data["raw_dir"]
    raw_dir.mkdir(parents=True, exist_ok=True)

    paths = {}
    for key, filename in data["files"].items():
        target = raw_dir / filename
        drive_id = data.get("drive_ids", {}).get(key)
        if not target.exists():
            if not drive_id:
                raise ValueError(f"No Drive file ID configured for '{key}'.")
            gdown.download(id=drive_id, output=str(target), quiet=False)
        paths[key] = target
    return paths
