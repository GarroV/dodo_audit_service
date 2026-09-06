"""Роутеры диалога: по одному на кусок сценария.

Порядок регистрации в диспетчере важен и задан в `src/bot/app.py`. Правило одно:
всё, что ждёт обычный текст в состоянии диалога, идёт раньше приёма материала,
иначе название пиццерии или новая формулировка записи будут перехвачены как
комментарий к кадру.
"""

from .correct import build_correct_router
from .edit import build_edit_router
from .fallback import build_fallback_router
from .finish import build_finish_router
from .info import build_info_router
from .material import build_material_router
from .mcp import build_mcp_router
from .record import build_record_router
from .records import build_records_router
from .start import build_start_router
from .version import build_version_router

__all__ = [
    "build_correct_router",
    "build_edit_router",
    "build_fallback_router",
    "build_finish_router",
    "build_info_router",
    "build_material_router",
    "build_mcp_router",
    "build_record_router",
    "build_records_router",
    "build_start_router",
    "build_version_router",
]
