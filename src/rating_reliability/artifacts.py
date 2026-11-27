"""Safe helpers for local, reproducible report artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4


class ArtifactError(Exception):
    """An expected local-artifact error suitable for concise CLI display."""


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    """Return a SHA-256 fingerprint while reading a file in bounded chunks."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def write_json_artifact(
    payload: Mapping[str, object], destination: Path, *, overwrite: bool = False
) -> None:
    """Write stable JSON through a temporary sibling and atomic replacement."""

    if destination.exists() and not overwrite:
        raise ArtifactError(
            f"output already exists; use --overwrite to replace: {destination}"
        )
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ArtifactError(f"cannot create output directory: {error}") from error

    temporary = destination.with_name(
        f".{destination.stem}.staging-{uuid4().hex}{destination.suffix}"
    )
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(payload, output, indent=2, sort_keys=True, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        if destination.exists() and not overwrite:
            raise ArtifactError(
                f"output appeared during generation; refusing to replace: {destination}"
            )
        os.replace(temporary, destination)
    except ArtifactError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise ArtifactError(f"cannot write JSON artifact: {error}") from error
    finally:
        temporary.unlink(missing_ok=True)
