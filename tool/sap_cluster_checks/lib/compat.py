"""
Python 3.6 compatibility shim for dataclasses.

On Python >= 3.7 this re-exports the stdlib symbols.
On Python 3.6 it provides minimal fallbacks sufficient for this project.
"""

try:
    from dataclasses import dataclass, field, asdict  # noqa: F401
except ImportError:
    # Fallback for Python < 3.7
    def field(default=None, default_factory=None):
        return default_factory() if default_factory else default

    def asdict(obj):
        """Convert dataclass to dict (recursive)."""
        result = {}
        for key in obj.__annotations__:
            value = getattr(obj, key, None)
            if hasattr(value, "__annotations__"):
                result[key] = asdict(value)
            elif isinstance(value, list):
                result[key] = [asdict(v) if hasattr(v, "__annotations__") else v for v in value]
            elif isinstance(value, dict):
                result[key] = {
                    k: asdict(v) if hasattr(v, "__annotations__") else v for k, v in value.items()
                }
            else:
                result[key] = value
        return result

    def dataclass(cls):
        """Simple dataclass decorator fallback."""
        original_annotations = getattr(cls, "__annotations__", {})

        def __init__(self, **kwargs):
            # Set defaults from class annotations first
            for name in original_annotations:
                default = getattr(cls, name, None)
                if callable(default) and not isinstance(default, type):
                    default = default()
                setattr(self, name, default)
            # Override with provided kwargs
            for key, value in kwargs.items():
                setattr(self, key, value)
            # Call __post_init__ if defined
            if hasattr(self, "__post_init__"):
                self.__post_init__()

        cls.__init__ = __init__
        cls.__annotations__ = original_annotations
        return cls
