# Mapová data

Databáze hranic (GeoJSON a TopoJSON) z OpenStreetMap pro [Mapový editor](https://mek2424.github.io/mapovy-editor/), kartu **Úpravy**.
Sporná území jsou upravená podle pravidel redakce ČT (stejných jako v editoru).

- **Česko** – co nejpodrobněji: kraje, okresy, ORP, obce s pověřeným úřadem, obce, městské části a obvody,
  správní obvody Prahy, katastrální území, vojenské újezdy, národní parky, CHKO, přírodní parky.
  Obce a další celky mají i sloupce s nadřazenými celky (POÚ, ORP, okres, kraj), takže je jde
  v editoru sloučit podle sloupce. Senátní obvody a povodí v OSM nejsou.
- **Svět** – obrysy států, 1. a 2. úroveň správních celků (úrovně se volí automaticky podle OSM).
  Obrysy jsou oříznuté podle pobřeží (pevnina z OSM, osmdata.openstreetmap.de) – bez pobřežních vod.

Každá vrstva má dvě podoby: **zjednodušenou** (`.json`, GeoJSON, rychlá) a **plnou** (`.topojson`, sousedé sdílejí hranice).
Ve světě se plná verze ukládá jen do 15 MB na soubor, aby se celý web vešel do limitu GitHub Pages (1 GB).
Sloupce: `id` (kód RÚIAN, ISO 3166-2 nebo id z OSM), `nazev` (česky, kde český název v OSM je).

Seznam všeho je v [`index.json`](https://mek2424.github.io/mapova-data/index.json), zprávy z generování ve složce `zpravy/`.

## Jak data vznikají

Generátor (`nastroje/generuj.py`) běží v GitHub Actions – jen tam je přístup ke Geofabriku. Data se berou z výřezů
Geofabriku po zemích (Overpass se z GitHubu ukázal jako nepoužitelný – neustálé chyby 504).

1. **Actions → Databáze GeoJSONů → Run workflow**, volba `pruzkum`. Vytvoří `zpravy/pruzkum-cesko.md`
   (jak jsou hranice v OSM označené) a `zpravy/pruzkum-sporna.md` (co se v OSM našlo pro sporná území).
   Podle toho se doladí `nastroje/vrstvy-cesko.json`.
2. `cesko` – vrstvy Česka (Geofabrik, asi 15 minut).
3. `svet` – státy světa z výřezů Geofabriku. Trvá hodiny; běh skončí po časovém limitu a příště **pokračuje**,
   kde přestal. Spouštět opakovaně, dokud `zpravy/souhrn.md` neukáže všechny státy.
4. `sporna` – redakční úpravy sporných území (`nastroje/sporna-uzemi.json`), výsledek v `zpravy/sporna-uzemi.md`.

Data se ukládají do větve **`data`**, kterou každý běh přepíše jedním commitem (úložiště tak nebobtná).
GitHub Pages musí servírovat právě tuhle větev: **Settings → Pages → Branch: `data`, složka `/ (root)`**.

## Licence

Data © přispěvatelé [OpenStreetMap](https://www.openstreetmap.org/copyright), licence ODbL –
v mapě musí zůstat uvedení zdroje „© přispěvatelé OpenStreetMap“.
