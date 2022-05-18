def get_unique_subclass(cls):
    classes = cls.__subclasses__()
    if len(classes) == 0:
        return cls
    elif len(classes) == 1:
        return classes[0]
    elif len(classes) > 1:
        raise RuntimeError(
            "This class should only be subclassed once by plugin!", cls, classes
        )
