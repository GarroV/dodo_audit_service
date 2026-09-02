"""Роутеры диалога: по одному на кусок сценария.

Порядок регистрации в диспетчере важен и задан в `src/bot/app.py`: мастер
начала проверки идёт первым, потому что его шаги ждут обычный текст, и приём
материала не должен перехватывать название пиццерии.
"""

from .material import build_material_router
from .start import build_start_router

__all__ = ["build_material_router", "build_start_router"]
