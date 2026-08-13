"""LLM-native web browser. QuickJS mini-DOM, not Chromium."""

from webresearch.agent import ask
from webresearch.client import Client
from webresearch.extract import extract
from webresearch.fetch import fetch
from webresearch.forms import forms
from webresearch.links import links
from webresearch.parse import parse
from webresearch.read import read
from webresearch.search import search
from webresearch.session import Browser, Session
from webresearch.snapshot import snapshot
from webresearch.tools import TOOLS

__all__ = [
    "Browser",
    "Client",
    "Session",
    "TOOLS",
    "ask",
    "extract",
    "fetch",
    "forms",
    "links",
    "parse",
    "read",
    "search",
    "snapshot",
]
__version__ = "0.3.0"
