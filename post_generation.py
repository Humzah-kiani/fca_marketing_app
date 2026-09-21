"""Backward-compatible shim for the current post studio implementation.

The app imports `post_studio` as the canonical generator, but older modules may
still import `post_generation`. Re-export the public API from here so both paths
remain stable.
"""

from post_studio import *  # noqa: F401,F403
