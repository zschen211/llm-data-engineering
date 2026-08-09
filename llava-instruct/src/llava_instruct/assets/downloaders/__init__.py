"""Download pipeline: download -> process -> persist.

Importing this package registers the built-in processors ("file", "parquet").
"""
from . import download, persist, process  # noqa: F401
from . import processors  # noqa: F401  (registers processors)
