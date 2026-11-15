---
inclusion: fileMatch
fileMatchPattern: "alphaforge/app/**"
---
# Règles de l'interface (alphaforge/app)

S'applique à l'interface Streamlit. Ne s'applique pas aux agents (`quant/`, `governance/`, `orchestrator/`, `explainability/`,
`enrichment/`), dont les messages sont vérifiés par leurs propres tests.

- L'interface n'a aucune logique métier : elle affiche les vues de `views.py`, qui lisent le registre. Aucun calcul de marché ici.
- Reformuler pour un lecteur débutant se fait à l'affichage seulement (`labels.py` : `human`, `humanize_text`, `window_name`).
  Les valeurs enregistrées, les codes et les messages des agents ne changent jamais ; un test vérifie que les nombres restent
  identiques après reformulation. Incident : un `.replace(".", ",")` sur un message entier a produit « SHOP,TO ».
- Toute chaîne visible passe par `i18n.t()` avec une clé en français ET en anglais (test de parité des clés).
- Un widget avec `key=` ne reçoit pas `value=` : on change sa valeur dans un rappel `on_click` (exécuté avant le rendu).
  Incident : les boutons de raccourci ne faisaient rien.
- Le rechargement à chaud (`_reload.py`) est désactivé sous pytest (`ALPHAFORGE_HOT_RELOAD=0`).
- Derrière le proxy de l'éditeur, lancer le serveur avec `--server.enableXsrfProtection false` : le cookie anti-falsification
  fait dépasser à nginx sa limite d'en-têtes (erreur 502 sur `_stcore/health`).
- Tester l'interface avec `streamlit.testing.v1.AppTest` (voir `tests/app/test_app_streamlit.py`).
