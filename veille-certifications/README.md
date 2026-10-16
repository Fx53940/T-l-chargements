# Veille quotidienne RNCP / RS

Importe chaque jour l'export officiel de France compétences (data.gouv.fr, schéma v4.1, Licence Ouverte 2.0), alimente une table de référence SQLite, détecte les nouvelles certifications et les changements, et exporte les échéances de renouvellement à relancer.

Python 3.9+, **aucune dépendance** (bibliothèque standard).

## Installation sur le VPS
```bash
cd veille-certifications
python3 -m unittest discover -s tests          # 6 tests, doivent passer
python3 veille_rncp_rs.py importer --db /var/lib/veille/veille.db   # 1er import = base de référence (~20 s, ~90 Mo de téléchargement)
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
| `importer --db DB --date D --rncp-zip A --rs-zip B` | Importe des zips déjà téléchargés (reprise, rejeu d'une date). |
| `nouveautes --db DB --depuis AAAA-MM-JJ [--out f.csv]` | Événements : `nouvelle`, `activee`, `desactivee`, `echeance_modifiee`, `modifiee`, `disparue`, `retour`. |
| `echeances --db DB --mois-min 0 --mois-max 24 [--inclure-de-droit] [--out f.csv]` | Certifications actives à renouveler dans la fenêtre, une ligne par certificateur. Par défaut : « sur demande » uniquement. |

CSV en UTF-8 avec BOM, séparateur `;` (ouverture directe dans Excel).

## Modèle de données (`schema.sql`)
- `certification` : une ligne par fiche (RNCP/RS), avec échéance (`date_fin_enregistrement`), durée, type d'enregistrement, première et dernière vue.
- `certificateur` / `certification_certificateur` : clé = SIRET, ou `NOM:<nom>` quand le SIRET est absent (environ 14 % des lignes actives « sur demande » de la fenêtre 2026-2028 n'en ont pas).
- `evenement` : journal des changements détectés d'un jour à l'autre.
- `import_log` : un enregistrement par import (date, empreinte SHA-256, compteurs).
- `v_echeances` : vue prête pour les relances (délai en mois calculé à la date du jour).
- `suivi_certificateur` : réservée à la future phase relance, non alimentée.

## Comportement à connaître
- **Le premier import crée la base de référence et aucun événement** (sinon 30 000 « nouveautés »). Les événements commencent au deuxième import.
- Une fiche absente de l'export du jour est marquée `disparue` ; si elle revient : événement `retour`.
- Un téléchargement coupé est repris (en-tête `Range`) : le proxy ou le réseau peuvent interrompre les gros fichiers (75 Mo pour le RNCP).
- Le niveau de qualification et les coordonnées de contact **ne figurent pas dans l'export** ; ils ne sont donc pas dans la base.
- Vérification effectuée : sur l'export du 15/10/2026, 30 689 fiches (25 778 RNCP + 4 911 RS), 6 071 certificateurs, 2 932 échéances « sur demande » entre le 16/10/2026 et le 31/12/2028 (le classeur Excel en compte 2 933 : une fiche y figurait deux fois pour le même certificateur).
