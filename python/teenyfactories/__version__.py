"""Version + build provenance for the teenyfactories package.

`__version__` tracks the package's semantic version. `__build_sha__` and
`__build_date__` are populated from environment variables baked into the
agent image at `docker build` time (see `core/python/build.sh` +
`Dockerfile.build`). Outside the image (eg. `pip install -e` for local dev)
they fall back to 'dev'.
"""

import os

__version__ = '1.0.0'
__build_sha__ = os.environ.get('TF_BUILD_SHA') or 'dev'
__build_date__ = os.environ.get('TF_BUILD_DATE') or 'dev'
