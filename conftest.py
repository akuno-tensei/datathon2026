"""Configuration pytest commune.

Le rechargement à chaud de l'interface (`alphaforge/app/_reload.py`) est désactivé pendant les tests : la suite garde des références
aux modules déjà importés, qu'une purge déclenchée par une modification de code concurrente rendrait incohérentes. Le test dédié
(`tests/app/test_hot_reload.py`) le réactive explicitement dans un processus séparé.
"""
import os

os.environ.setdefault("ALPHAFORGE_HOT_RELOAD", "0")
# Pas d'appel réseau à Bedrock pendant les tests : l'interprétation par règles est déterministe. Les tests du client utilisent des doublures.
os.environ.setdefault("ALPHAFORGE_LLM", "0")
# Pas d'archivage S3 pendant les tests (les tests de l'archive utilisent un client factice).
os.environ.setdefault("ALPHAFORGE_S3_BUCKET", "")
