"""Backward-compatible legacy import shim for the renamed post_studio module."""

from post_studio import build_post_canvas, post_studio_ui

__all__ = ["build_post_canvas", "post_studio_ui"]
