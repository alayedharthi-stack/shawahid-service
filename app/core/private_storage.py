"""Reserve a non-public subtree on the existing single Railway volume."""
from pathlib import Path

from starlette.staticfiles import StaticFiles

PRIVATE_SUBDIR = ".worksheet-private"


class PublicStorageFiles(StaticFiles):
    """Deny the reserved tree, including aliases resolving into it."""

    def lookup_path(self, path):
        if PRIVATE_SUBDIR.casefold() in {p.casefold() for p in Path(path).parts}:
            return "", None
        full_path, stat_result = super().lookup_path(path)
        if full_path:
            candidate = Path(full_path).resolve()
            for directory in self.all_directories:
                private = (Path(directory) / PRIVATE_SUBDIR).resolve()
                if candidate == private or private in candidate.parents:
                    return "", None
        return full_path, stat_result


def worksheet_private_root(settings):
    root = Path(settings.WORKSHEET_PILOT_PRIVATE_DIR).resolve()
    public = settings.storage_path.resolve()
    reserved = public / PRIVATE_SUBDIR
    static = Path("app/static").resolve()
    if root == static or static in root.parents or root in static.parents:
        raise ValueError("pilot_storage_must_be_private")
    if root != reserved and (root == public or public in root.parents or root in public.parents):
        raise ValueError("pilot_storage_must_be_private")
    if getattr(settings, "APP_ENV", "development") == "production":
        # A configured path is not evidence of durability. Refuse activation
        # unless the existing volume is actually mounted at runtime.
        if root != reserved or not public.is_mount():
            raise ValueError("worksheet_persistent_volume_required")
    return root
