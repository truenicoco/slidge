# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#
import os
import sys

# sys.path.insert(0, os.path.abspath('.'))
# sys.path.insert(0, os.path.abspath('..'))
# sys.path.insert(0, os.path.abspath('../..'))


# -- Project information -----------------------------------------------------

project = "Slidge"
copyright = "2021, Nicolas Cedilnik"
author = "Nicolas Cedilnik"


# -- General configuration ---------------------------------------------------

# Add any Sphinx extension module names here, as strings. They can be
# extensions coming with Sphinx (named 'sphinx.ext.*') or your custom
# ones.
extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.extlinks",
    "sphinx.ext.viewcode",
    "sphinx.ext.autodoc.typehints",
    "sphinxarg.ext",
    "autoapi.extension",
]

autodoc_typehints = "description"

# Incldude __init__ docstrings
autoclass_content = "both"
autoapi_python_class_content = "both"

autoapi_type = "python"
autoapi_dirs = ["../../slidge"]
autoapi_add_toctree_entry = False
autoapi_keep_files = False
autoapi_root = "dev/api"
autoapi_ignore = ["*xep_*"]

# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = []


# -- Options for HTML output -------------------------------------------------

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]

intersphinx_mapping = {
    "python": (
        "https://docs.python.org/3",
        None,
    ),
    "slixmpp": ("https://slixmpp.readthedocs.io/en/latest/", None),
    "aiosignald": ("https://aiosignald.readthedocs.io/en/latest/", None),
}

extlinks = {
    "xep": ("https://xmpp.org/extensions/xep-%s.html", "XEP-"),
    "issue": ("https://github.com/sphinx-doc/sphinx/issues/%s", "issue "),
}
