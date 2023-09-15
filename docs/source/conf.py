# Configuration file for the Sphinx documentation builder.
#
# This file only contains a selection of the most common options. For a full
# list see the documentation:
# https://www.sphinx-doc.org/en/master/usage/configuration.html
from enum import Enum, IntEnum

from slixmpp import ComponentXMPP

# -- Path setup --------------------------------------------------------------

# If extensions (or modules to document with autodoc) are in another directory,
# add these directories to sys.path here. If the directory is relative to the
# documentation root, use os.path.abspath to make it absolute, like shown here.
#

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
    "sphinx.ext.autosectionlabel",
    "sphinx.ext.autodoc",
    "sphinx.ext.intersphinx",
    "sphinx.ext.extlinks",
    # "sphinx.ext.viewcode",  # crashes build unfortunately
    "sphinx.ext.autodoc.typehints",
    "sphinx.ext.autosectionlabel",
    "sphinxarg.ext",
    "autoapi.extension",
]

autodoc_typehints = "description"

# Incldude __init__ docstrings
autoclass_content = "both"
autoapi_python_class_content = "both"

autoapi_type = "python"
autoapi_dirs = ["../../slidge", "../../superduper"]
autoapi_add_toctree_entry = False
autoapi_keep_files = False
autoapi_root = "dev/api"
autoapi_ignore = ["*xep_*", "slidge/core/*"]
autoapi_options = [
    "members",
    "show-module-summary",
    "inherited-members",
    "imported-members",
    # these on-by-default parameters are disabled
    # "undoc-members",
    # "private-members",
    # "show-inheritance",
    # "special-members",
]


def skip_util_classes(app, what, name, obj, skip, options):
    # avoid listing parent methods in the public API docs
    if any(name.endswith("Gateway." + x) for x in dir(ComponentXMPP)):
        skip = True
    elif any(
        name.endswith("MucType." + x) or name.endswith("CommandAccess." + x)
        for x in dir(int) + dir(Enum) + dir(IntEnum) + ["name", "value"]
    ):
        skip = True
    return skip


def setup(sphinx):
    sphinx.connect("autoapi-skip-member", skip_util_classes)


# Add any paths that contain templates here, relative to this directory.
templates_path = ["_templates"]

# List of patterns, relative to source directory, that match files and
# directories to ignore when looking for source files.
# This pattern also affects html_static_path and html_extra_path.
exclude_patterns = []


# -- Options for HTML output -------------------------------------------------

intersphinx_mapping = {
    "python": (
        "https://docs.python.org/3",
        None,
    ),
    "slixmpp": ("https://slixmpp.readthedocs.io/en/latest/", None),
    "aiosignald": ("https://aiosignald.readthedocs.io/en/latest/", None),
}

extlinks = {"xep": ("https://xmpp.org/extensions/xep-%s.html", "XEP-%s")}
html_theme = "furo"
