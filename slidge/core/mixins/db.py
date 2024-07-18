from contextlib import contextmanager


class UpdateInfoMixin:
    """
    This mixin just adds a context manager that prevents commiting to the DB
    on every attribute change.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._updating_info = False

    @contextmanager
    def updating_info(self):
        self._updating_info = True
        yield
        self._updating_info = False
