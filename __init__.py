try:
    from .main import Main
except ModuleNotFoundError as exc:
    if exc.name != "astrbot":
        raise
    Main = None

__all__ = ["Main"]
