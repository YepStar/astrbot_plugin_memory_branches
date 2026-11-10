"""Bili's memory gateway: Memory Branches is the sole authoritative backend."""

_BOOT_BACKEND = None


def backend(bot):
    global _BOOT_BACKEND
    star = bot.context.get_registered_star("astrbot_plugin_memory_branches")
    if star is not None:
        if getattr(star, "activated", True) is False:
            raise RuntimeError("Memory Branches is disabled")
        instance = getattr(star, "star_cls", None)
        service = getattr(instance, "public_memory", None)
        if service is not None:
            return service
    # Bili initializes before some other plugins. Read the same new database,
    # never a legacy fallback, during this short startup interval.
    if _BOOT_BACKEND is None:
        from types import SimpleNamespace
        from astrbot.api.star import StarTools
        from data.plugins.astrbot_plugin_memory_branches.engine import Engine
        from data.plugins.astrbot_plugin_memory_branches.compat import PublicMemory

        engine = Engine(
            StarTools.get_data_dir("astrbot_plugin_memory_branches")
            / "memory_branches.db"
        )
        bank = engine.route("bilibili:public")
        if not bank:
            raise RuntimeError("Public memory bank has not been migrated")
        _BOOT_BACKEND = PublicMemory(
            SimpleNamespace(engine=engine, kick=lambda bank: None), bank
        )
    return _BOOT_BACKEND


def records(bot):
    return backend(bot).bili_records()
