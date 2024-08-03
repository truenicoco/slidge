from .admin import MucAdminMixin
from .mam import MamMixin
from .misc import MucMiscMixin
from .owner import MucOwnerMixin


class MucMixin(MamMixin, MucAdminMixin, MucOwnerMixin, MucMiscMixin):
    pass


__all__ = ("MucMixin",)
