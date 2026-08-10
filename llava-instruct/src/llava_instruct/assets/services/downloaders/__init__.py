"""Download pipeline: download -> process -> persist.

Importing this package registers the built-in processors ("file", "parquet").
"""

from . import (  # noqa: F401
    download,
    persist,
    process,
    processors,
)
