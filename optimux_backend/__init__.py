"""Optimux backend.

This file runs before any other module of the package, so settings that other
libraries read when they are imported belong here.
"""
import os

# LiteLLM downloads its model price list from GitHub when it is imported, unless
# told to use the copy it ships with. Optimux never needs that download: without
# a network it held up every start by a minute or more, and it is a connection
# nobody asked for ("Nothing leaks").
os.environ["LITELLM_LOCAL_MODEL_COST_MAP"] = "True"
