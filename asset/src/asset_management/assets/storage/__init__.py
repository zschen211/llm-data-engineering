"""Storage backends for multimodal blobs.

Two-layer layout: the raw mirror layer (``raw/<source_id>/<path_in_repo>``,
path-addressed) and the final asset layer (``blobs/<sha256[:2]>/<sha256><ext>``,
content-addressed so the same content is stored exactly once).
"""

from .base import StorageBackend, object_key_for, raw_key_for
from .local import LocalStorageBackend
from .s3 import S3StorageBackend

__all__ = [
    "LocalStorageBackend",
    "S3StorageBackend",
    "StorageBackend",
    "object_key_for",
    "raw_key_for",
]
