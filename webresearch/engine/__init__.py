"""From-scratch browser engine: tiny DOM + QuickJS. Not Chromium."""

from webresearch.engine.captcha import dump_captcha_code, inspect_captcha
from webresearch.engine.frames import load_frames
from webresearch.engine.inspect import click_html, inspect_html, type_html
from webresearch.engine.runtime import run_scripts

__all__ = [
    "click_html",
    "dump_captcha_code",
    "inspect_captcha",
    "inspect_html",
    "load_frames",
    "run_scripts",
    "type_html",
]
