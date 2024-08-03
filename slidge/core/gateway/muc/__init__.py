from .admin import MucAdminMixin
from .misc import MucMiscMixin
from .owner import MucOwnerMixin


class MucMixin(MucAdminMixin, MucOwnerMixin, MucMiscMixin):
    pass


__all__ = ("MucMixin",)
