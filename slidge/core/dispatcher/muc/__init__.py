from .admin import MucAdminMixin
from .mam import MamMixin
from .misc import MucMiscMixin
from .owner import MucOwnerMixin
from .ping import PingMixin


class MucMixin(PingMixin, MamMixin, MucAdminMixin, MucOwnerMixin, MucMiscMixin):
    pass


__all__ = ("MucMixin",)
