# Instantané SEC EDGAR (démo hors ligne)

- **Contenu** : pour les 10 actions américaines de l'univers, les 20 derniers dépôts réglementaires (formulaires 10-K, 10-Q,
  8-K… : métadonnées et liens, pas le texte) et les fondamentaux du dernier rapport annuel (chiffre d'affaires, bénéfice net,
  flux de trésorerie d'exploitation, trésorerie, dette à long terme, capitaux propres, actif total, en dollars US).
- **Source** : SEC EDGAR, API publiques `data.sec.gov` (dépôts et données XBRL « companyfacts ») et `www.sec.gov`.
  Source officielle : niveau de confiance A dans l'application.
- **Date des données** : rien de publié après le 12 novembre 2026 (dernière date du jeu de départ). Date de collecte :
  voir `manifest.json`. Pour une date d'analyse antérieure, un rapport déposé après cette date n'est pas utilisé : la
  donnée devient une lacune, jamais une valeur supposée.
- **Traitements** : aucun calcul. Les chiffres sont repris tels quels du rapport annuel ; un indicateur absent du rapport est
  omis. Le concept XBRL retenu pour chaque indicateur est consigné (`xbrl_concepts`).
- **Lacune connue** : XOM. La SEC associe aujourd'hui le symbole à un nouveau déclarant (CIK 2115436) qui n'a encore déposé
  aucun rapport annuel : pas de fondamentaux, seulement ses dépôts.
- **Conditions** : données publiques de la SEC, consultables et téléchargeables gratuitement. La collecte automatisée suit la
  politique d'accès équitable de la SEC (identification de l'appelant, au plus 10 requêtes par seconde).
- **Régénérer** : `ALPHAFORGE_SEC_USER_AGENT="Nom contact@exemple.org" python3 alphaforge_starter/build_sec_snapshot.py`.
  L'identification n'est écrite dans aucun fichier.
