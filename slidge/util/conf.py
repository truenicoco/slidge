import logging
from functools import cached_property
from types import GenericAlias
from typing import Optional, Union, get_args, get_origin, get_type_hints

import configargparse


class Option:
    DOC_SUFFIX = "__DOC"
    DYNAMIC_DEFAULT_SUFFIX = "__DYNAMIC_DEFAULT"
    SHORT_SUFFIX = "__SHORT"

    def __init__(self, parent: "ConfigModule", name: str):
        self.parent = parent
        self.config_obj = parent.config_obj
        self.name = name

    @cached_property
    def doc(self):
        return getattr(self.config_obj, self.name + self.DOC_SUFFIX)

    @cached_property
    def required(self):
        return not hasattr(
            self.config_obj, self.name + self.DYNAMIC_DEFAULT_SUFFIX
        ) and not hasattr(self.config_obj, self.name)

    @cached_property
    def default(self):
        return getattr(self.config_obj, self.name, None)

    @cached_property
    def short(self):
        return getattr(self.config_obj, self.name + self.SHORT_SUFFIX, None)

    @cached_property
    def nargs(self):
        type_ = get_type_hints(self.config_obj).get(self.name, type(self.default))

        if isinstance(type_, GenericAlias):
            args = get_args(type_)
            if args[1] is Ellipsis:
                return "*"
            else:
                return len(args)

    @cached_property
    def type(self):
        type_ = get_type_hints(self.config_obj).get(self.name, type(self.default))

        if _is_optional(type_):
            type_ = get_args(type_)[0]
        elif isinstance(type_, GenericAlias):
            args = get_args(type_)
            type_ = args[0]

        return type_

    @cached_property
    def names(self):
        res = ["--" + self.name.lower().replace("_", "-")]
        if s := self.short:
            res.append("-" + s)
        return res

    @cached_property
    def kwargs(self):
        kwargs = dict(
            required=self.required,
            help=self.doc,
            env_var=self.name_to_env_var(),
        )
        t = self.type
        if t is bool:
            if self.default:
                kwargs["action"] = "store_false"
            else:
                kwargs["action"] = "store_true"
        else:
            kwargs["type"] = t
            if self.required:
                kwargs["required"] = True
            else:
                kwargs["default"] = self.default
        if n := self.nargs:
            kwargs["nargs"] = n
        return kwargs

    def name_to_env_var(self):
        return self.parent.ENV_VAR_PREFIX + self.name


class ConfigModule:
    ENV_VAR_PREFIX = "SLIDGE_"

    def __init__(
        self, config_obj, parser: Optional[configargparse.ArgumentParser] = None
    ):

        self.config_obj = config_obj
        if parser is None:
            parser = configargparse.ArgumentParser()
        self.parser = parser

        self.add_options_to_parser()

    def _list_options(self):
        return {
            o
            for o in (set(dir(self.config_obj)) | set(get_type_hints(self.config_obj)))
            if o.upper() == o and not o.startswith("_") and "__" not in o
        }

    def set_conf(self, argv=None):
        args, rest = self.parser.parse_known_args(argv)
        self.update_dynamic_defaults(args)
        for name in self._list_options():
            value = getattr(args, name.lower())
            log.debug("Setting '%s' to %r", name, value)
            setattr(self.config_obj, name, value)
        return args, rest

    @cached_property
    def options(self):
        res = []
        for opt in self._list_options():
            res.append(Option(self, opt))
        return res

    def add_options_to_parser(self):
        p = self.parser
        for o in self.options:
            p.add_argument(*o.names, **o.kwargs)

    def update_dynamic_defaults(self, args):
        pass


def _is_optional(t):
    if get_origin(t) is Union:
        args = get_args(t)
        if len(args) == 2 and isinstance(None, args[1]):
            return True
    return False


log = logging.getLogger(__name__)
