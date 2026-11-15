"""Configuration pytest commune.

Le rechargement à chaud de l'interface (`alphaforge/app/_reload.py`) est désactivé pendant les tests : la suite garde des références
aux modules déjà importés, qu'une purge déclenchée par une modification de code concurrente rendrait incohérentes. Le test dédié
(`tests/app/test_hot_reload.py`) le réactive explicitement dans un processus séparé.
"""
import os

os.environ.setdefault("ALPHAFORGE_HOT_RELOAD", "0")
