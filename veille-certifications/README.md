# Veille quotidienne RNCP / RS

Importe chaque jour l'export officiel de France compétences (data.gouv.fr, schéma v4.1, Licence Ouverte 2.0), alimente la base de référence (SQLite), détecte les nouvelles certifications et les changements, rattache chaque certification aux entreprises qui la portent, et exporte les échéances de renouvellement à relancer.

Python 3.9+, **aucune dépendance** (bibliothèque standard). Le schéma de la base est `schema.sql` (validé le 16/10/2026). Les contacts, relances et commentaires ne sont **pas** dans cette base : ils vivent dans Pipedrive.

## Installation sur le VPS
```bash
cd veille-certifications
python3 -m unittest discover -s tests            # 14 tests, doivent passer
python3 veille_rncp_rs.py importer --db /var/lib/veille/veille.db   # 1er import = base de référence (~30 s, ~90 Mo téléchargés)
```
Planification quotidienne (l'export est publié vers 2 h UTC) :
```cron
30 4 * * *  cd /opt/veille-certifications && python3 veille_rncp_rs.py importer --db /var/lib/veille/veille.db >> /var/log/veille.log 2>&1
```
L'import est idempotent : relancé le même jour, il ne fait rien.

## Commandes
| Commande | Rôle |
|---|---|
| `importer --db DB [--date AAAA-MM-JJ]` | Télécharge l'export le plus récent (ou celui de la date) et met la base à jour. |
| `importer … --rncp-zip A --rs-zip B --date D` | Importe des zips déjà téléchargés (reprise, rejeu d'une date). |
| `nouveautes --db DB --depuis AAAA-MM-JJ [--out f.csv]` | Événements : `nouvelle`, `activee`, `desactivee`, `echeance_modifiee`, `modifiee`, `disparue`, `retour`. |
| `echeances --db DB --mois-min 0 --mois-max 24 [--sur-demande-seulement] [--out f.csv]` | Une ligne par certification et par entreprise : échéance, tranche, rôle, site web, co-certificateurs. Les universités (« de droit ») sont incluses par défaut ; les entreprises exclues ne sont jamais proposées. |
| `qualification --db DB` | Cas à trancher par une personne (certificateurs sans SIRET, sites ambigus). |
| `exclusions --db DB` | Règles d'exclusion, statut, nombre d'entreprises touchées. |

CSV en UTF-8 avec BOM, séparateur `;` (ouverture directe dans Excel).

## Modèle de données
- **`certification`** : une ligne par fiche, avec les 23 champs retenus (niveau, NSF, dates, statistiques, lien…), plus `premiere_vue`, `derniere_vue`, `import_id` et `fiche_de_reference`. Listes associées : `certification_formacode`, `certification_rome`, `certification_filiation`, `certification_correspondance`.
- **`certification_historique`** : toute valeur suivie qui change (intitulé, état, activité, type d'enregistrement, niveau, échéance, limite de délivrance, durée, liste des certificateurs) est conservée avec sa période de validité.
- **`entreprise`** (clé SIREN, ou `NOM:…` si le SIRET manque) et **`etablissement`** (SIRET). Un même SIREN sous plusieurs SIRET = une seule entreprise.
- **`certification_etablissement`** : liaison historisée (`valide_au` vide = en vigueur) avec le **rôle** : `unique`, `leader`, `co` ou `indetermine`.
- **`entreprise_site_web`** : site déduit, avec source, statut (`a_valider`, `valide`, `invalide`) et dates. Une valeur validée ou saisie à la main n'est jamais écrasée.
- **`evenement`**, **`import_log`**, **`audit_log`** : journaux. **`file_qualification`** : ce qu'une personne doit trancher. **`exclusion_regle`** : exclusions. **`pipedrive_lien`** : réservée à la phase Pipedrive, non alimentée.
- **`v_opportunites`** : vue de travail, une ligne par certification et par entreprise, hors entreprises exclues, avec délai en mois et tranche (`moins_12_mois`, `12_24_mois`, `24_mois_et_plus`).

## Règles
- **Leader d'une certification co-certifiée** (règles validées) : 1) l'entreprise dont le site correspond au lien de la fiche ; 2) sinon celle qui a le plus de certifications actives ; 3) sinon **sans leader** (`indetermine`). Tous les organismes restent contactables.
- **Site web** : déduit des fiches à **un seul** certificateur. Un domaine est retenu s'il pèse au moins 70 % des fiches de l'entreprise et n'est pas un portail (France compétences, Legifrance, réseaux sociaux…) ni partagé par plus de 3 entreprises. Sinon, l'entreprise va dans la file `site_ambigu`.
- **Ministères** : exclus via `exclusions.csv` (liste de SIREN, plus un repérage par le nom pour les seuls certificateurs sans SIRET). **Toutes les règles sont livrées au statut `a_valider` et ne sont donc pas appliquées** tant que la colonne `statut` n'est pas passée à `valide`. `--appliquer-a-valider` permet de mesurer leur effet avant validation.
- **Premier import** : base de référence, aucun événement (sinon 30 000 « nouveautés »). Ses fiches portent `fiche_de_reference = 1` : leur vraie date d'apparition est la date de décision officielle. Les événements commencent au deuxième import.
- **Une fiche absente de l'export du jour** est marquée `disparue` ; si elle revient : événement `retour`.
- Un téléchargement coupé est repris (en-tête `Range`).

## Vérifications effectuées (export du 15/10/2026)
- 30 689 fiches (25 778 RNCP, 4 911 RS), 6 578 actives, 5 350 entreprises, 6 070 établissements. Import complet en 30 s, base de 69 Mo.
- Fenêtre 16/10/2026 – 31/12/2028, fiches « sur demande », ministères exclus : **2 035 certifications, 1 346 entreprises**, 2 717 couples certification × entreprise (2 910 si l'on compte par SIRET). Avec les universités et les autres « de droit » : 2 758 certifications, 1 483 entreprises, 6 493 couples.
- Niveau de qualification : renseigné pour les 5 078 fiches RNCP actives ; 596 fiches RNCP inactives portent « Sans niveau ».
- Deuxième jour simulé à l'échelle réelle (une échéance modifiée, une fiche retirée, une fiche ajoutée) : exactement ces trois événements et une ligne d'historique, aucun parasite sur les autres fiches.
- Le téléchargement automatique n'a pas pu être testé de bout en bout depuis l'environnement de développement (accès à data.gouv.fr coupé par le proxy) ; sa logique de reprise est testée sur un serveur local.

## Limites connues
- L'export ne contient ni contacts, ni adresse, ni effectif, ni indication du siège social (`est_siege` reste vide). L'enrichissement par les données publiques est une étape ultérieure.
- Le site web est déduit pour environ la moitié des entreprises de la fenêtre ; le reste demande une recherche complémentaire.
- Le premier import ne permet pas de dater l'apparition des fiches existantes.
