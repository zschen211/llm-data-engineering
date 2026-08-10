"""Storage backends for multimodal blobs.

Object keys are content-addressed: ``blobs/<sha256[:2]>/<sha256><ext>`` so the
same content is stored exactly once and the key is bound to the content.
"""

from .base import StorageBackend, object_key_for
from .local import LocalStorageBackend
from .s3 import S3StorageBackend

__all__ = [
    "LocalStorageBackend",
    "S3StorageBackend",
    "StorageBackend",
    "object_key_for",
]
