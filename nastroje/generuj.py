#!/usr/bin/env python3
"""Generátor databáze GeoJSONů z OpenStreetMap pro Mapový editor (mek2424/mapovy-editor).

Spouští se v GitHub Actions (.github/workflows/data.yml), kde je přístup k Geofabriku a Overpassu.

  python nastroje/generuj.py pruzkum   – zjistí, jak jsou hranice v OSM označené (zpravy/*.md)
  python nastroje/generuj.py cesko     – vrstvy Česka podle nastroje/vrstvy-cesko.json
  python nastroje/generuj.py svet      – státy + 1. a 2. úroveň správních celků (navazuje, kde skončil)
  python nastroje/generuj.py svet-vybrane --staty PRT,MKD – znovu jen vybrané státy
  python nastroje/generuj.py nazvy     – názvy v hotových datech světa do češtiny (bez stahování)
  python nastroje/generuj.py sporna    – redakční úpravy sporných území (nastroje/sporna-uzemi.json)
  python nastroje/generuj.py index     – index.json pro editor

Každá vrstva má dvě podoby: zjednodušenou (GeoJSON, rychlá) a plnou (TopoJSON, sousedé
sdílejí hranice). Sloupce: id, nazev, u podřízených celků i názvy nadřazených.
Data © přispěvatelé OpenStreetMap, licence ODbL.
"""
import argparse
import datetime as dt
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import osmium
from shapely import make_valid
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, box, mapping, shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

from prepis import jazyk_statu, ma_cyrilici, prepis

NASTROJE = Path(__file__).resolve().parent
# Výstup jde do složky VYSTUP (v Actions je to větev „data“, kterou servírují GitHub Pages).
KOREN = Path(os.environ.get('VYSTUP') or NASTROJE.parent).resolve()
DATA = KOREN / 'data'
ZPRAVY = KOREN / 'zpravy'
PRACOVNI = Path(os.environ.get('PRACOVNI', tempfile.gettempdir())) / 'mapova-data'

UA = 'mapova-data/1.0 (+https://github.com/mek2424/mapova-data)'
OVERPASS = [
    'https://overpass-api.de/api/interpreter',
    'https://overpass.private.coffee/api/interpreter',
    'https://maps.mail.ru/osm/tools/overpass/api/interpreter',
]
GEOFABRIK_CESKO = 'https://download.geofabrik.de/europe/czech-republic-latest.osm.pbf'
STATY_EDITORU = 'https://raw.githubusercontent.com/mek2424/mapovy-editor/main/data/oblasti/staty.json'
SPORNA_EDITORU = 'https://raw.githubusercontent.com/mek2424/mapovy-editor/main/data/sporna_uzemi.geojson'
NAZVY_EDITORU = 'https://raw.githubusercontent.com/mek2424/mapovy-editor/main/js/nazvy.js'
MAPSHAPER = ['mapshaper'] if shutil.which('mapshaper') else ['npx', '-y', 'mapshaper@0.7.90']

# Zjednodušená verze: kolik bodů zůstane (mapshaper -simplify) a přesnost souřadnic.
# Zjednodušení podle cílového rozlišení (jako obrázek 4000 × 4000 px přes rozsah vrstvy): u malých států
# i u Ruska vypadá stejně podrobně a jednoduché tvary (obdélník) se nezdeformují, což procenta bodů dělala.
ZJEDNODUSENI = 'resolution=4000x4000'
# Plné verze ve světě: jemné zjednodušení s tolerancí 20 m (pobřeží z OSM má body po pár metrech,
# bez toho by se databáze nevešla do 1 GB GitHub Pages). Česko zůstává bez zjednodušení.
PLNA_SVET = ['-simplify', 'interval=20', 'keep-shapes']
PRESNOST_ZJEDNODUSENE = '0.00001'   # ~1 m
PRESNOST_PLNE = '0.000001'          # ~10 cm
LIMIT_SOUBORU = 95 * 1024 * 1024    # GitHub nepřijme soubor nad 100 MB
# GitHub Pages mají limit 1 GB na celý web – plné verze ve světě jen do této velikosti.
LIMIT_PLNE_SVET = int(os.environ.get('LIMIT_PLNE_SVET_MB', '15')) * 1024 * 1024
# Pevnina z OSM (osmdata.openstreetmap.de, land-polygons-split-4326) – ořez obrysů ve světě podle pobřeží.
# Hranice států a krajů v OSM vedou po moři (pobřežní vody 12 mil); redakce chce obrysy jen po pevnině.
PEVNINA_URL = 'https://osmdata.openstreetmap.de/download/land-polygons-split-4326.zip'


def log(*a):
    print(time.strftime('%H:%M:%S'), *a, flush=True)


# ------------------------------------------------------------------ síť

def stahni(url, cil=None, *, data=None, pokusy=4, pauza=20, timeout=1800):
    """Stáhne URL (volitelně POST data). Vrací bajty, nebo zapíše do souboru cil."""
    posledni = None
    for pokus in range(pokusy):
        try:
            req = urllib.request.Request(url, data=data, headers={'User-Agent': UA, 'Accept': '*/*'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                if cil:
                    with open(cil, 'wb') as f:
                        shutil.copyfileobj(r, f, length=1 << 20)
                    return cil
                return r.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            posledni = e
            kod = getattr(e, 'code', None)
            if kod in (400, 404):
                raise
            log(f'  stažení selhalo ({e}), pokus {pokus + 1}/{pokusy}')
            time.sleep(pauza * (pokus + 1))
    raise RuntimeError(f'{url}: {posledni}')


def overpass(dotaz, cil=None, *, timeout=1800):
    """Dotaz na Overpass s náhradními servery. Při chybě 429/504 počká a zkusí jiný server."""
    posledni = None
    for kolo in range(3):
        for url in OVERPASS:
            try:
                return stahni(url, cil, data=urllib.parse.urlencode({'data': dotaz}).encode(), pokusy=1, timeout=timeout)
            except Exception as e:  # noqa: BLE001 – zkusit další server
                posledni = e
                log(f'  Overpass {url.split("/")[2]}: {e}')
        time.sleep(60 * (kolo + 1))
    raise RuntimeError(f'Overpass neodpovídá: {posledni}')


# ------------------------------------------------------------------ OSM → plochy

def plochy(soubor, *filtry, prijmout=None):
    """Plochy z OSM souboru (pbf nebo xml): multipolygony z relací i uzavřené cesty.
    Vrací seznam {osm, tagy, geom}. Enklávy a díry skládá osmium správně (role inner).
    prijmout(tagy) vyřadí nepotřebné plochy dřív, než se z nich počítá geometrie.
    Pozor: Overpass musí vracet prvky setříděné (out body; ne „qt“), jinak osmium odmítne soubor."""
    fab = osmium.geom.WKBFactory()
    from shapely import wkb
    vysledek = []
    fp = osmium.FileProcessor(str(soubor)).with_areas(*filtry)
    for o in fp:
        if not isinstance(o, osmium.osm.Area):
            continue
        if prijmout and not prijmout(o.tags):
            continue
        try:
            g = wkb.loads(fab.create_multipolygon(o), hex=True)
        except Exception as e:  # noqa: BLE001 – rozbitá relace v OSM
            log(f'  přeskočeno {"w" if o.from_way() else "r"}{o.orig_id()}: {e}')
            continue
        g = jen_plochy(g)
        if g.is_empty:
            continue
        vysledek.append({'osm': f'{"w" if o.from_way() else "r"}{o.orig_id()}', 'tagy': dict(o.tags), 'geom': g})
    return vysledek


def jen_plochy(g):
    if not g.is_valid:
        g = make_valid(g)
    if isinstance(g, (Polygon, MultiPolygon)):
        return g
    if isinstance(g, GeometryCollection):
        casti = [x for x in g.geoms if isinstance(x, (Polygon, MultiPolygon))]
        return unary_union(casti) if casti else Polygon()
    return Polygon()


_opravy = None


def opravy_editoru():
    """Opravy názvů z editoru (js/nazvy.js, OPRAVY): {chybný tvar: správný}, např. Republika Krym."""
    global _opravy
    if _opravy is None:
        text = stahni(NAZVY_EDITORU).decode('utf-8')
        _opravy = dict(re.findall(r'"([^"]+)"\s*:\s*"([^"]+)"', re.search(r'export const OPRAVY = \{(.*?)\};', text, re.S).group(1)))
    return _opravy


def je_latinka(text):
    return all(not z.isalpha() or unicodedata.name(z, '').startswith('LATIN') for z in text)


def prevod_nazvu(jmeno, iso3, tagy=None):
    """Název celku ve světě do češtiny: z cyrilice přepis podle ÚJČ (nastroje/prepis.py), u jiných písem
    anglický název latinkou, nakonec opravy z editoru. Český název z OSM (name:cs) má přednost už v cesky_nazev.
    Slovník regionů z editoru se nepoužívá: dával by v jedné vrstvě vedle sebe české exonymum a přepis."""
    tagy = tagy or {}
    vysledek = None
    if ma_cyrilici(jmeno):
        jazyk = jazyk_statu(iso3)
        kandidati = [(tagy.get(f'name:{jazyk}') or jmeno, jazyk)] if jazyk else []
        kandidati.append((tagy.get('name:ru'), 'ru'))  # jiné jazyky s cyrilicí: jen ruský název
        for kandidat, jaz in kandidati:
            if kandidat and ma_cyrilici(kandidat):
                vysledek = prepis(kandidat, jaz)
                if vysledek:
                    break
    if vysledek is None and jmeno and not je_latinka(jmeno):
        vysledek = next((tagy[k] for k in ('name:en', 'int_name', 'name:latin') if tagy.get(k) and je_latinka(tagy[k])), None)
    vysledek = vysledek or jmeno
    return opravy_editoru().get(vysledek, vysledek)


def cesky_nazev(tagy, iso3=None):
    if iso3 is None:
        return tagy.get('name:cs') or tagy.get('name') or tagy.get('name:en') or ''
    if tagy.get('name:cs'):
        return opravy_editoru().get(tagy['name:cs'], tagy['name:cs'])
    return prevod_nazvu(tagy.get('name') or tagy.get('name:en') or '', iso3, tagy)


# ------------------------------------------------------------------ výstup

def mapshaper(*argumenty):
    p = subprocess.run([*MAPSHAPER, *argumenty], capture_output=True, text=True)
    if p.returncode:
        raise RuntimeError(f'mapshaper {" ".join(argumenty)}: {p.stderr[-2000:]}')
    return p.stderr


def uloz_vrstvu(slozka, klic, prvky, limit_plne=LIMIT_SOUBORU):
    """Zapíše vrstvu: <klic>.json (zjednodušená GeoJSON) a <klic>.topojson (plná). Vrací metadata."""
    slozka.mkdir(parents=True, exist_ok=True)
    prvky = [p for p in prvky if not p['geom'].is_empty]
    prvky.sort(key=lambda p: (p['vlastnosti'].get('nazev') or '').lower())
    surovy = PRACOVNI / f'{slozka.name}-{klic}.geojson'
    surovy.parent.mkdir(parents=True, exist_ok=True)
    with open(surovy, 'w', encoding='utf-8') as f:
        json.dump({'type': 'FeatureCollection', 'features': [
            {'type': 'Feature', 'properties': p['vlastnosti'], 'geometry': mapping(p['geom'])} for p in prvky]}, f, ensure_ascii=False)
    zjednodusena = slozka / f'{klic}.json'
    plna = slozka / f'{klic}.topojson'
    mapshaper('-i', str(surovy), 'encoding=utf8', '-simplify', ZJEDNODUSENI, 'keep-shapes',
              '-o', str(zjednodusena), 'format=geojson', 'geojson-type=FeatureCollection', f'precision={PRESNOST_ZJEDNODUSENE}')
    # Kvantování na milion kroků přes rozsah vrstvy: přesnost ~1 m u státu, soubor několikrát menší.
    jemne = PLNA_SVET if 'svet' in slozka.relative_to(DATA).parts else []
    mapshaper('-i', str(surovy), 'encoding=utf8', *jemne, '-o', str(plna), 'format=topojson', 'quantization=1000000')
    if plna.stat().st_size > limit_plne:
        log(f'  {klic}: plná verze má {plna.stat().st_size >> 20} MB, je nad limitem {limit_plne >> 20} MB – zůstane jen zjednodušená')
        plna.unlink()
    surovy.unlink()
    sloupce = sorted({k for p in prvky for k in p['vlastnosti']})
    meta = {
        'prvku': len(prvky),
        'soubory': {'zjednodusena': str(zjednodusena.relative_to(KOREN))},
        'velikost': {'zjednodusena': zjednodusena.stat().st_size},
        'sloupce': sloupce,
    }
    if plna.exists():
        meta['soubory']['plna'] = str(plna.relative_to(KOREN))
        meta['velikost']['plna'] = plna.stat().st_size
    return meta


def zmensi_svet():
    """Jednorázově zjednoduší už vygenerované plné verze ve světě (tolerance 20 m) a opraví velikosti v meta.json."""
    pred = po = 0
    for meta_soubor in sorted((DATA / 'svet').rglob('meta.json')):
        meta = json.loads(meta_soubor.read_text(encoding='utf-8'))
        for klic, v in meta.items():
            plna = v.get('soubory', {}).get('plna') if isinstance(v, dict) else None
            if not plna:
                continue
            cesta = KOREN / plna
            pred += cesta.stat().st_size
            docasny = cesta.with_suffix('.novy.topojson')
            mapshaper('-i', str(cesta), *PLNA_SVET, '-o', str(docasny), 'format=topojson', 'quantization=1000000')
            docasny.replace(cesta)
            v['velikost']['plna'] = cesta.stat().st_size
            po += cesta.stat().st_size
        meta_soubor.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
    log(f'Plné verze světa: {pred >> 20} MB → {po >> 20} MB')


def nacti_vrstvu(cesta):
    """Načte zjednodušenou i plnou vrstvu zpět (pro úpravy sporných území)."""
    with open(cesta, encoding='utf-8') as f:
        fc = json.load(f)
    return [{'vlastnosti': x['properties'], 'geom': jen_plochy(shape(x['geometry']))} for x in fc['features'] if x.get('geometry')]


def nacti_plnou(slozka, klic):
    """Plná vrstva z TopoJSONu (přes mapshaper), jinak zjednodušená."""
    plna = slozka / f'{klic}.topojson'
    if not plna.exists():
        return nacti_vrstvu(slozka / f'{klic}.json')
    docasny = PRACOVNI / f'{slozka.name}-{klic}-zpet.json'
    docasny.parent.mkdir(parents=True, exist_ok=True)
    mapshaper('-i', str(plna), '-o', str(docasny), 'format=geojson', 'geojson-type=FeatureCollection')
    prvky = nacti_vrstvu(docasny)
    docasny.unlink()
    return prvky


def doplnit_nadrazene(prvky, rodice, sloupec):
    """Každému prvku doplní název nadřazeného celku (podle reprezentativního bodu)."""
    if not rodice:
        return
    strom = STRtree([r['geom'] for r in rodice])
    for p in prvky:
        bod = p['geom'].representative_point()
        for i in strom.query(bod):
            if rodice[i]['geom'].contains(bod):
                p['vlastnosti'][sloupec] = rodice[i]['vlastnosti']['nazev']
                break


# ------------------------------------------------------------------ Česko

def nastaveni_ceska():
    with open(NASTROJE / 'vrstvy-cesko.json', encoding='utf-8') as f:
        return json.load(f)['vrstvy']


def plati(podminka, tagy):
    for k, v in podminka.get('tagy', {}).items():
        if tagy.get(k) != v:
            return False
    if 'admin_level' in podminka and tagy.get('admin_level') not in podminka['admin_level']:
        return False
    if 'nazev' in podminka:
        jmeno = tagy.get('name') or ''
        if not re.search(podminka['nazev'], jmeno):
            return False
    if not podminka.get('tagy') and 'nazev' not in podminka:
        return False
    return True


def soubor_ceska():
    cil = PRACOVNI / 'czech-republic-latest.osm.pbf'
    if not cil.exists() or time.time() - cil.stat().st_mtime > 20 * 3600:
        cil.parent.mkdir(parents=True, exist_ok=True)
        log('Stahuji Česko z Geofabriku…')
        stahni(GEOFABRIK_CESKO, cil)
    return cil


def plochy_ceska():
    pbf = soubor_ceska()
    log('Skládám plochy hranic a chráněných území…')
    vse = plochy(pbf, osmium.filter.KeyFilter('boundary'), prijmout=lambda t: 'boundary' in t)
    # Výřez z Geofabriku má okraj i se sousedy – nechat jen to, co leží v Česku.
    stat = [p for p in vse if p['tagy'].get('admin_level') == '2' and p['tagy'].get('ISO3166-1') == 'CZ']
    if not stat:
        log('  obrys Česka nenalezen – ořez se nepoužije')
        return vse
    cesko = unary_union([p['geom'] for p in stat]).buffer(0.0005)
    strom_ok = [p for p in vse if cesko.contains(p['geom'].representative_point())]
    log(f'  v Česku {len(strom_ok)} z {len(vse)} ploch')
    return strom_ok


def cesko():
    vrstvy = nastaveni_ceska()
    vse = plochy_ceska()
    log(f'{len(vse)} ploch')
    vybrane = {}
    for v in vrstvy:
        prvky = []
        vyloucit = re.compile(v['vylouc_nazev']) if v.get('vylouc_nazev') else None
        for p in vse:
            if not any(plati(podm, p['tagy']) for podm in v['kdyz']):
                continue
            if vyloucit and vyloucit.search(p['tagy'].get('name', '')):
                continue
            kod = next((p['tagy'][k] for k in v.get('id', []) if p['tagy'].get(k)), p['osm'])
            nazev = cesky_nazev(p['tagy'])
            if v.get('odstranit'):
                nazev = re.sub(v['odstranit'], '', nazev).strip() or nazev
            prvky.append({'vlastnosti': {'id': kod, 'nazev': nazev}, 'geom': p['geom'], 'osm': p['osm']})
        # Stejná hranice může být v OSM dvakrát (relace i cesta) – nechat jednu.
        videne = set(); jedinecne = []
        for p in prvky:
            k = (p['vlastnosti']['nazev'], round(p['geom'].area, 9))
            if k not in videne:
                videne.add(k); jedinecne.append(p)
        vybrane[v['klic']] = jedinecne
        log(f'  {v["klic"]}: {len(jedinecne)} prvků')
    slozka = DATA / 'CZE'
    meta = {}
    for v in vrstvy:
        prvky = vybrane[v['klic']]
        if not prvky:
            log(f'  {v["klic"]}: nic nenalezeno – vrstva se nevytvoří (viz průzkum)')
            continue
        for rodic in v.get('nadrazene', []):
            doplnit_nadrazene(prvky, vybrane.get(rodic, []), {'kraje': 'kraj', 'okresy': 'okres', 'obce': 'obec'}.get(rodic, rodic))
        meta[v['klic']] = {'nazev': v['nazev'], 'skupina': v['skupina'], 'popis': v.get('popis', ''), **uloz_vrstvu(slozka, v['klic'], prvky)}
        log(f'  uloženo {v["klic"]}')
    slozka.mkdir(parents=True, exist_ok=True)
    meta_soubor = slozka / 'meta.json'
    with open(meta_soubor, 'w', encoding='utf-8') as f:
        json.dump({'vytvoreno': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'), 'vrstvy': meta}, f, ensure_ascii=False, indent=1)


# ------------------------------------------------------------------ průzkum

def pruzkum():
    ZPRAVY.mkdir(exist_ok=True)
    vse = plochy_ceska()
    radky = ['# Průzkum hranic v OSM – Česko', '', f'Vytvořeno {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC z Geofabriku. Plochy se značkou boundary: {len(vse)}.', '']
    skupiny = defaultdict(list)
    for p in vse:
        t = p['tagy']
        klic = (t.get('boundary', ''), t.get('admin_level', ''), t.get('protect_class', ''), t.get('border_type', ''))
        skupiny[klic].append(p)
    radky += ['## Skupiny podle značek', '', '| boundary | admin_level | protect_class | border_type | počet | příklady názvů | značky s kódy |', '|---|---|---|---|---|---|---|']
    for klic, ps in sorted(skupiny.items(), key=lambda x: -len(x[1])):
        priklady = ', '.join(sorted({p['tagy'].get('name', '?') for p in ps})[:6])
        kody = Counter(k for p in ps for k in p['tagy'] if k.startswith('ref') or 'code' in k)
        radky.append(f'| {klic[0]} | {klic[1]} | {klic[2]} | {klic[3]} | {len(ps)} | {priklady} | {", ".join(f"{k} ({n})" for k, n in kody.most_common(5))} |')
    radky += ['', '## Co najdou nastavené vrstvy (nastroje/vrstvy-cesko.json)', '', '| vrstva | počet | příklady |', '|---|---|---|']
    for v in nastaveni_ceska():
        ps = [p for p in vse if any(plati(podm, p['tagy']) for podm in v['kdyz'])]
        radky.append(f'| {v["klic"]} | {len(ps)} | {", ".join(sorted({p["tagy"].get("name", "?") for p in ps})[:5])} |')
    radky += ['', '## Názvy s klíčovými slovy (pro doladění filtrů)', '']
    for slovo in ['obvod', 'ORP', 'pověřen', 'újezd', 'senát', 'povodí', 'park', 'Praha ', 'katastr']:
        ps = [p for p in vse if slovo.lower() in p['tagy'].get('name', '').lower()]
        znacky = Counter(f"{p['tagy'].get('boundary', '-')}/{p['tagy'].get('admin_level', '-')}" for p in ps)
        radky.append(f'- **{slovo}**: {len(ps)} ploch, značky {dict(znacky.most_common(6))}, např. {", ".join(sorted({p["tagy"]["name"] for p in ps})[:5])}')
    (ZPRAVY / 'pruzkum-cesko.md').write_text('\n'.join(radky) + '\n', encoding='utf-8')
    log('zpravy/pruzkum-cesko.md hotovo')
    pruzkum_sporna()


def najdi_oblast(jmena, kody=()):
    """Sporná oblast podle name:en (nebo name, int_name) ve výřezech Geofabriku států `kody` (např. Rusko a Ukrajina).
    Vrací (geometrie, popis nalezeného) nebo (None, důvod). Overpass se nepoužívá – z GitHubu nefunguje."""
    staty = {x['id']: x for x in jedinecne_staty()}
    nalezene = []; videne = set()
    for kod in kody:
        if kod not in staty:
            continue
        for p in hranice_extraktu(extrakt_pro(staty[kod])):
            if p['osm'] in videne:
                continue
            # Indické státy mají anglický název jen v „name“ (bez name:en) – proto i ten.
            if any(p['tagy'].get(k) in jmena for k in ('name:en', 'int_name', 'name')):
                videne.add(p['osm']); nalezene.append(p)
    if not nalezene:
        return None, f've výřezech {", ".join(kody)} nenalezeno'
    # Více relací (např. ukrajinská i ruská správa Krymu): spojit, ať je pokrytá celá oblast.
    popis = '; '.join(f"{p['osm']} {p['tagy'].get('name', '')} (admin_level={p['tagy'].get('admin_level', '-')})" for p in nalezene)
    return unary_union([p['geom'] for p in nalezene]), popis


def pruzkum_sporna():
    with open(NASTROJE / 'sporna-uzemi.json', encoding='utf-8') as f:
        pravidla = json.load(f)['pravidla']
    radky = ['# Průzkum sporných území v OSM', '', 'Které relace OSM se pro pravidla z nastroje/sporna-uzemi.json najdou ve výřezech Geofabriku dotčených států.', '']
    for pr in pravidla:
        radky.append(f'## {pr["nazev"]}')
        for o in pr.get('oblasti', []):
            try:
                g, popis = najdi_oblast(o['name:en'], (pr['z'], pr['do']))
            except Exception as e:  # noqa: BLE001
                radky.append(f'- {o["nazev"]}: chyba {e}')
                continue
            radky.append(f'- {o["nazev"]}: {popis}' + (f'; plocha {g.area:.3f}°²' if g is not None else ''))
        if pr['druh'] == 'rovnobezka':
            radky.append(f'- dělí se stát {pr["stat"]} po {pr["sirka"]}° s. š.')
        radky.append('')
    ZPRAVY.mkdir(exist_ok=True)
    (ZPRAVY / 'pruzkum-sporna.md').write_text('\n'.join(radky) + '\n', encoding='utf-8')
    log('zpravy/pruzkum-sporna.md hotovo')


# ------------------------------------------------------------------ svět

PREPSANE_UROVNE = {}  # nastroje/urovne-svet.json: {"ISO3": [L1, L2]} – když automatika vybere špatně


def staty_editoru():
    return json.loads(stahni(STATY_EDITORU))


def jedinecne_staty():
    """Státy s kódem ISO. Natural Earth má některé kódy dvakrát (zámořská území) – nechat ten s víc regiony."""
    podle_iso = {}
    for s in staty_editoru():
        if len(s.get('iso', '')) != 2:
            continue
        if s['iso'] not in podle_iso or s.get('regiony', 0) > podle_iso[s['iso']].get('regiony', 0):
            podle_iso[s['iso']] = s
    return sorted(podle_iso.values(), key=lambda s: s['id'])


GEOFABRIK_INDEX = 'https://download.geofabrik.de/index-v1.json'
_index_geofabrik = None
_hranice_cache = {}


def index_geofabrik():
    global _index_geofabrik
    if _index_geofabrik is None:
        _index_geofabrik = json.loads(stahni(GEOFABRIK_INDEX))['features']
    return _index_geofabrik


def extrakt_pro(s):
    """Nejvhodnější výřez Geofabriku pro stát: podle kódu ISO (celý stát, ne jeho část),
    jinak nejmenší výřez, který obsahuje střed státu."""
    idx = index_geofabrik()
    kandidati = [f for f in idx if s['iso'] in (f['properties'].get('iso3166-1:alpha2') or []) and f.get('geometry')]
    if kandidati:
        return max(kandidati, key=lambda f: shape(f['geometry']).area)['properties']
    x1, y1, x2, y2 = s['bbox']
    from shapely.geometry import Point
    stred = Point((x1 + x2) / 2, (y1 + y2) / 2)
    obsahuji = [f for f in idx if f.get('geometry') and shape(f['geometry']).contains(stred)]
    if not obsahuji:
        raise RuntimeError('pro stát není výřez Geofabriku')
    nejmensi = min(obsahuji, key=lambda f: shape(f['geometry']).area)['properties']
    if not nejmensi.get('parent'):
        # Jen celý kontinent (Afrika má přes 6 GB) – zpracování by zabralo hodiny běhu.
        raise RuntimeError(f'stát nemá vlastní výřez Geofabriku, jen celý kontinent ({nejmensi["id"]}) – vynecháno')
    return nejmensi


def hranice_extraktu(vlastnosti):
    """Správní hranice (boundary=administrative) z výřezu Geofabriku jako plochy. Stažený výřez
    se hned zúží nástrojem osmium a smaže, takže na disku zůstávají jen malé soubory hranic."""
    url = vlastnosti['urls']['pbf']
    if url in _hranice_cache:
        return _hranice_cache[url]
    jmeno = re.sub(r'[^a-z0-9]+', '-', vlastnosti['id'].lower())
    hranice = PRACOVNI / 'extrakty' / f'{jmeno}-hranice.osm.pbf'
    hranice.parent.mkdir(parents=True, exist_ok=True)
    if not hranice.exists():
        surovy = PRACOVNI / 'extrakty' / f'{jmeno}.osm.pbf'
        log(f'  stahuji {url}')
        stahni(url, surovy, timeout=3600)
        subprocess.run(['osmium', 'tags-filter', str(surovy), 'r/boundary=administrative', '-o', str(hranice), '--overwrite', '--no-progress'], check=True)
        surovy.unlink()
    vysledek = plochy(hranice, osmium.filter.TagFilter(('boundary', 'administrative')), prijmout=lambda t: t.get('boundary') == 'administrative')
    if len(_hranice_cache) > 6:
        _hranice_cache.clear()
    _hranice_cache[url] = vysledek
    return vysledek


MIN_POKRYTI = 0.85  # úroveň musí pokrýt aspoň tolik plochy státu (Portugalsko: na úrovni 4 jsou jen Azory a Madeira)


def zvol_urovne(pocty, iso3, pokryti=None):
    if iso3 in PREPSANE_UROVNE:
        return PREPSANE_UROVNE[iso3]
    pokryti = pokryti or {}
    dost = lambda u: pokryti.get(u, 1.0) >= MIN_POKRYTI
    urovne = sorted(pocty)
    l1 = next((u for u in urovne if pocty[u] >= 2 and dost(u)), None)
    if l1 is None:
        l1 = next((u for u in urovne if pocty[u] >= 2), None)
    if l1 is None:
        return []
    l2 = next((u for u in urovne if u > l1 and pocty[u] > pocty[l1] and pocty[u] <= 6000 and dost(u)), None)
    return [l1] if l2 is None else [l1, l2]


def stat_ze_sveta(s, slozka):
    iso2 = s['iso']
    vlastnosti = extrakt_pro(s)
    vse = hranice_extraktu(vlastnosti)
    je_stat = lambda t: iso2 in (t.get('ISO3166-1'), t.get('ISO3166-1:alpha2'))
    obrys = [p for p in vse if je_stat(p['tagy']) and p['tagy'].get('admin_level') == '2']
    if not obrys:
        # Závislá území (Aruba, Ålandy…) mají v OSM kód ISO na nižší úrovni.
        jine = sorted((p for p in vse if je_stat(p['tagy']) and p['tagy'].get('admin_level', '').isdigit()), key=lambda p: int(p['tagy']['admin_level']))
        obrys = jine[:1]
    if obrys:
        obrys_g = unary_union([p['geom'] for p in obrys])
        uroven_statu = int(obrys[0]['tagy'].get('admin_level', '2'))
    else:
        # Hranice státu se ve výřezu nesložila (zámořská území, hranice na moři mimo výřez):
        # obrys = sjednocení nejvyšších celků, které leží uvnitř státu podle obrysu Natural Earth z editoru.
        ne = obrys_natural_earth(s['id']).buffer(0.05)
        celky = defaultdict(list)
        for p in vse:
            u = p['tagy'].get('admin_level', '')
            if u.isdigit() and 2 < int(u) <= 6 and ne.contains(p['geom'].representative_point()):
                celky[int(u)].append(p)
        if not celky:
            raise RuntimeError(f'obrys státu ve výřezu {vlastnosti["id"]} nenalezen ani z jeho celků')
        nejvyssi = min(celky)
        obrys_g = jen_plochy(unary_union([p['geom'] for p in celky[nejvyssi]]))
        uroven_statu = nejvyssi - 1
        zapis_prubeh(f'{s["id"]}: obrys státu složen z celků admin_level {nejvyssi} ({len(celky[nejvyssi])})')
    okoli = obrys_g.buffer(0.01)
    obrys_pevnina = na_pevninu(obrys_g)
    if obrys_pevnina.is_empty:
        obrys_pevnina = obrys_g  # stát bez pevniny v datech (např. jen útesy) – nechat, jak je
    meta = {'stat': {'nazev': 'Obrys státu', 'skupina': 'Správní členění', **uloz_vrstvu(slozka, 'stat', [{'vlastnosti': {'id': s['id'], 'nazev': s['nazev']}, 'geom': obrys_pevnina}], LIMIT_PLNE_SVET)}}

    uvnitr = defaultdict(list)
    for p in vse:
        # Jen relace: uzavřené cesty se značkami hranice jsou úseky hranic nebo ostrůvky
        # (Portugalsko jich mělo 466 jako „celky“ 1. úrovně), ne správní celky.
        if not p['osm'].startswith('r'):
            continue
        u = p['tagy'].get('admin_level', '')
        if u.isdigit() and uroven_statu < int(u) <= 10 and okoli.contains(p['geom'].representative_point()):
            uvnitr[int(u)].append(p)
    pocty = {u: len(ps) for u, ps in uvnitr.items()}
    plocha_statu = obrys_g.area or 1
    pokryti = {u: min(1.0, sum(p['geom'].area for p in ps) / plocha_statu) for u, ps in uvnitr.items()}
    urovne = zvol_urovne(pocty, s['id'], pokryti)
    meta['_urovne'] = {'pocty': dict(sorted(pocty.items())), 'pokryti': {u: round(v, 3) for u, v in sorted(pokryti.items())},
                       'zvolene': urovne, 'vyrez': vlastnosti['id']}
    predchozi = None
    for i, uroven in enumerate(urovne, start=1):
        prvky = []
        videne = set()
        for p in uvnitr[uroven]:
            if p['osm'] in videne:
                continue
            videne.add(p['osm'])
            kod = p['tagy'].get('ISO3166-2') or p['tagy'].get('ref') or p['osm']
            geom = na_pevninu(p['geom'])
            if geom.is_empty:
                continue  # celek jen na moři
            prvky.append({'vlastnosti': {'id': kod, 'nazev': cesky_nazev(p['tagy'], s['id'])}, 'geom': geom})
        if i == 2 and predchozi:
            doplnit_nadrazene(prvky, predchozi, 'uroven1')
        typ = Counter(p['tagy'].get('border_type', '') for p in uvnitr[uroven]).most_common(1)
        meta[f'uroven{i}'] = {
            'nazev': f'{i}. úroveň', 'skupina': 'Správní členění',
            'popis': f'admin_level {uroven}' + (f' ({typ[0][0]})' if typ and typ[0][0] else ''),
            **uloz_vrstvu(slozka, f'uroven{i}', prvky, LIMIT_PLNE_SVET),
        }
        predchozi = prvky
    return meta


_pevnina = None


def pevnina():
    """Všechny kousky pevniny (~800 tisíc polygonů do 1°) ve stromu STRtree. Načte se jednou za běh."""
    global _pevnina
    if _pevnina is not None:
        return _pevnina
    import shapely
    slozka = PRACOVNI / 'pevnina'
    shp = next(slozka.rglob('land_polygons.shp'), None) if slozka.exists() else None
    if shp is None:
        if os.environ.get('BEZ_OREZU'):
            _pevnina = False
            return _pevnina
        slozka.mkdir(parents=True, exist_ok=True)
        zip_ = slozka / 'land.zip'
        log('Stahuji pevninu z osmdata.openstreetmap.de…')
        try:
            stahni(PEVNINA_URL, zip_, timeout=3600)
        except Exception:
            zip_.unlink(missing_ok=True)  # nedostažený soubor nesmí zůstat (ani v mezipaměti Actions)
            raise
        subprocess.run(['unzip', '-q', '-o', str(zip_), '-d', str(slozka)], check=True)
        zip_.unlink()
        shp = next(slozka.rglob('land_polygons.shp'))
    import pyogrio
    _, _, wkb_, _ = pyogrio.raw.read(str(shp), columns=[])
    kusy = shapely.from_wkb(wkb_)
    _pevnina = (kusy, shapely.STRtree(kusy))
    log(f'  pevnina: {len(kusy)} kousků')
    return _pevnina


def na_pevninu(g):
    """Ořízne plochu podle pobřeží. Kousky pevniny celé uvnitř se jen spojí (rychlé),
    přesně se počítají jen ty, které hranici plochy kříží."""
    p = pevnina()
    if not p or g.is_empty:
        return g
    import shapely
    kusy, strom = p
    uvnitr = strom.query(g, predicate='contains')
    kandidati = strom.query(g, predicate='intersects')
    hranicni = np_setdiff(kandidati, uvnitr)
    casti = []
    if len(uvnitr):
        try:
            casti.append(shapely.coverage_union_all(kusy[uvnitr]))
        except Exception:  # noqa: BLE001 – kousky nejsou čisté pokrytí
            try:
                casti.append(shapely.union_all(kusy[uvnitr]))
            except shapely.errors.GEOSException:
                casti.append(shapely.union_all(shapely.make_valid(kusy[uvnitr]), grid_size=1e-9))
    plochy_kusu = shapely.area(kusy[hranicni]) if len(hranicni) else []
    for i, plocha_kusu in zip(hranicni, plochy_kusu):
        kus = kusy[i]
        vyrez = shapely.clip_by_rect(g, *kus.bounds)
        if vyrez.is_empty:
            continue
        x1, y1, x2, y2 = kus.bounds
        if plocha_kusu >= (x2 - x1) * (y2 - y1) * 0.99999:
            # Kousek pevniny je celý čtverec (vnitrozemí) – výřez obdélníkem stačí, přesný průnik
            # se složitou hranicí by byl zbytečně pomalý (Polsko trvalo 70 minut).
            casti.append(vyrez)
            continue
        try:
            casti.append(vyrez.intersection(kus))
        except shapely.errors.GEOSException:
            casti.append(shapely.make_valid(vyrez).intersection(shapely.make_valid(kus)))
    if not casti:
        return Polygon()
    try:
        return jen_plochy(shapely.union_all(casti))
    except shapely.errors.GEOSException:
        # Topologická chyba (Kanada, Grónsko) – opravit tvary a sjednotit znovu.
        return jen_plochy(shapely.union_all([shapely.make_valid(c) for c in casti], grid_size=1e-9))


def np_setdiff(a, b):
    import numpy as np
    return np.setdiff1d(a, b, assume_unique=False)


def obrys_natural_earth(iso3):
    """Hrubý obrys státu z editoru (Natural Earth) – jen k rozhodnutí, které celky do státu patří."""
    j = json.loads(stahni(f'https://raw.githubusercontent.com/mek2424/mapovy-editor/main/data/oblasti/stat/{iso3}.json'))
    return jen_plochy(shape(j['geometry']))


def zapis_prubeh(radek):
    """Průběh stahování světa – v Actions nejsou záznamy z běhu odjinud vidět."""
    ZPRAVY.mkdir(parents=True, exist_ok=True)
    with open(ZPRAVY / 'prubeh-svet.md', 'a', encoding='utf-8') as f:
        f.write(f'- {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} {radek}\n')


def svet(minut, znovu=False, doplnit_plne=False, vybrane=()):
    global PREPSANE_UROVNE
    for kod in vybrane:
        # Znovu vygenerovat jen tyto státy. Stará data zůstanou, dokud se nepřepíšou.
        meta_soubor = DATA / 'svet' / kod / 'meta.json'
        if meta_soubor.exists():
            log(f'Znovu na vyžádání: {kod}')
            # Záloha: když nové generování selže (např. výpadek zdroje), vrátí se stará data.
            meta_soubor.replace(meta_soubor.with_name('meta.zaloha.json'))
    if doplnit_plne:
        # Státy, u kterých se některá plná verze nevešla do limitu – vygenerovat znovu
        # (od zavedení jemného zjednodušení jsou plné verze menší). Stará data zůstanou, dokud se nepřepíšou.
        for meta_soubor in sorted((DATA / 'svet').glob('*/meta.json')):
            meta = json.loads(meta_soubor.read_text(encoding='utf-8'))
            if any(isinstance(v, dict) and 'soubory' in v and 'plna' not in v['soubory'] for v in meta.values()):
                log(f'Znovu kvůli plné verzi: {meta_soubor.parent.name}')
                meta_soubor.replace(meta_soubor.with_name('meta.zaloha.json'))
    if znovu:
        log('Mažu dosavadní data světa – generuje se od začátku.')
        shutil.rmtree(DATA / 'svet', ignore_errors=True)
        (ZPRAVY / 'prubeh-svet.md').unlink(missing_ok=True)
    prepsane = NASTROJE / 'urovne-svet.json'
    if prepsane.exists():
        PREPSANE_UROVNE = {k: v for k, v in json.loads(prepsane.read_text(encoding='utf-8')).items() if not k.startswith('_')}
    konec = time.time() + minut * 60
    staty = jedinecne_staty()
    hotovo = 0
    for s in staty:
        slozka = DATA / 'svet' / s['id']
        if (slozka / 'meta.json').exists():
            continue
        if time.time() > konec:
            log(f'Časový limit – pokračuje se příště ({hotovo} hotovo v tomto běhu).')
            break
        log(f'{s["id"]} {s["nazev"]}')
        zacatek = time.time()
        try:
            meta = stat_ze_sveta(s, slozka)
        except Exception as e:  # noqa: BLE001 – zapsat a pokračovat dalším státem
            log(f'  chyba: {e}')
            chyby = DATA / 'svet' / 'chyby.json'
            z = json.loads(chyby.read_text(encoding='utf-8')) if chyby.exists() else {}
            z[s['id']] = f'{dt.date.today()}: {e}'
            zapis_prubeh(f'{s["id"]} {s["nazev"]}: CHYBA po {time.time() - zacatek:.0f} s – {e}')
            chyby.parent.mkdir(parents=True, exist_ok=True)
            chyby.write_text(json.dumps(z, ensure_ascii=False, indent=1), encoding='utf-8')
            zaloha = slozka / 'meta.zaloha.json'
            if zaloha.exists():
                zaloha.replace(slozka / 'meta.json')
                log('  vrácena předchozí data státu')
                zapis_prubeh(f'{s["id"]}: vrácena předchozí data')
            continue
        meta['vytvoreno'] = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
        (slozka / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
        (slozka / 'meta.zaloha.json').unlink(missing_ok=True)
        hotovo += 1
        chyby = DATA / 'svet' / 'chyby.json'
        if chyby.exists():
            z = json.loads(chyby.read_text(encoding='utf-8'))
            if z.pop(s['id'], None) is not None:
                chyby.write_text(json.dumps(z, ensure_ascii=False, indent=1), encoding='utf-8')
        zapis_prubeh(f'{s["id"]} {s["nazev"]}: {time.time() - zacatek:.0f} s, úrovně {meta["_urovne"]["zvolene"]}, '
                     + ', '.join(f'{k} {v["prvku"]}' for k, v in meta.items() if isinstance(v, dict) and 'prvku' in v))
    # Státy, které skončily chybou (bez vlastního výřezu apod.), se znovu zkoušejí v dalších bězích,
    # ale nesmějí blokovat vrstvu „Státy“ – ta se složí z toho, co je hotové.
    chyby = DATA / 'svet' / 'chyby.json'
    s_chybou = set(json.loads(chyby.read_text(encoding='utf-8'))) if chyby.exists() else set()
    zbyva = [s['id'] for s in staty if not (DATA / 'svet' / s['id'] / 'meta.json').exists() and s['id'] not in s_chybou]
    if not zbyva:
        staty_sveta(staty)
    log(f'Zbývá {len(zbyva)} států (a {len(s_chybou)} s chybou).')


def staty_sveta(staty):
    """Vrstva „Svět – všechny státy“ z plných obrysů jednotlivých států (včetně území ze sporných pravidel)."""
    prvky = []
    kody = {s['id'] for s in staty} | {p.name for p in (DATA / 'svet').glob('*/') if (p / 'stat.json').exists()}
    for kod in sorted(kody):
        slozka = DATA / 'svet' / kod
        if (slozka / 'stat.json').exists():
            prvky += nacti_plnou(slozka, 'stat')
    meta = {'staty': {'nazev': 'Státy', 'skupina': 'Správní členění', **uloz_vrstvu(DATA / 'svet', 'staty', prvky, LIMIT_PLNE_SVET * 3)}}
    (DATA / 'svet' / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')


# ------------------------------------------------------------------ sporná území

def sporna():
    with open(NASTROJE / 'sporna-uzemi.json', encoding='utf-8') as f:
        pravidla = json.load(f)['pravidla']
    zprava = ['# Úpravy sporných území', '', f'Provedeno {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC.', '']
    for pr in pravidla:
        zprava.append(f'## {pr["nazev"]}')
        if pr['druh'] == 'prevest':
            for o in pr['oblasti']:
                g, popis = najdi_oblast(o['name:en'], (pr['z'], pr['do']))
                if g is None:
                    zprava.append(f'- {o["nazev"]}: {popis} – beze změny')
                    continue
                zprava.append(f'- {o["nazev"]}: {popis}')
                zprava += prevest(pr['z'], pr['do'], na_pevninu(g), o['nazev'])
        elif pr['druh'] == 'rovnobezka':
            zprava += rovnobezka(pr)
        zprava.append('')
    if (DATA / 'svet' / 'meta.json').exists():
        staty_sveta(jedinecne_staty())
    ZPRAVY.mkdir(exist_ok=True)
    (ZPRAVY / 'sporna-uzemi.md').write_text('\n'.join(zprava) + '\n', encoding='utf-8')


def prejmenuj_vrstvu(slozka, klic, uprav):
    """Změní jen vlastnosti prvků (názvy) v obou souborech vrstvy – geometrie zůstává beze změny,
    nic se znovu nezjednodušuje. uprav(vlastnosti) vrací True, když něco změnila. Vrací počet změněných prvků."""
    zmeneno = 0
    velikosti = {}
    for druh, soubor in (('zjednodusena', slozka / f'{klic}.json'), ('plna', slozka / f'{klic}.topojson')):
        if not soubor.exists():
            continue
        data = json.loads(soubor.read_text(encoding='utf-8'))
        if 'features' in data:
            vlastnosti = [f.setdefault('properties', {}) for f in data['features']]
        else:
            vlastnosti = [g.setdefault('properties', {}) for o in data.get('objects', {}).values() for g in o.get('geometries', [])]
        pocet = sum(1 for v in vlastnosti if v is not None and uprav(v))
        if pocet:
            soubor.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
            velikosti[druh] = soubor.stat().st_size
        zmeneno = max(zmeneno, pocet)
    if velikosti:
        meta_soubor = slozka / 'meta.json'
        meta = json.loads(meta_soubor.read_text(encoding='utf-8'))
        if isinstance(meta.get(klic), dict):
            meta[klic].setdefault('velikost', {}).update(velikosti)
            meta_soubor.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
    return zmeneno


def nazvy():
    """Převede názvy celků v hotových datech světa do češtiny (prevod_nazvu) bez nového generování.
    Názvy v jiném písmu než latinka a cyrilice tu zůstanou – anglický název se doplní až při generování státu."""
    zprava = ['# Převod názvů ve světě do češtiny', '',
              f'Provedeno {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC. Cyrilice podle Internetové jazykové příručky ÚJČ '
              '(nastroje/prepis.py), opravy podle editoru (js/nazvy.js, OPRAVY).', '']
    celkem = 0
    jine_pismo = {}
    for meta_soubor in sorted((DATA / 'svet').glob('*/meta.json')):
        slozka = meta_soubor.parent
        iso3 = slozka.name
        prevody = {}

        def uprav(prvni):
            def f(v):
                zmena = False
                stary = v.get('nazev') or ''
                novy = prevody.get(stary) if prvni and stary in prevody else prevod_nazvu(stary, iso3)
                if novy != stary:
                    if prvni:
                        prevody[stary] = novy
                    v['nazev'] = novy
                    zmena = True
                if not prvni and v.get('uroven1'):
                    rodic = v['uroven1']
                    novy_rodic = prevody.get(rodic) or prevod_nazvu(rodic, iso3)
                    if novy_rodic != rodic:
                        v['uroven1'] = novy_rodic
                        zmena = True
                return zmena
            return f

        zmeny = {}
        priklady = []
        for klic, prvni in (('uroven1', True), ('uroven2', False)):
            pred = dict(prevody)
            n = prejmenuj_vrstvu(slozka, klic, uprav(prvni))
            if n:
                zmeny[klic] = n
            if prvni:
                priklady += [f'{a} → {b}' for a, b in list(prevody.items())[:4] if a not in pred]
        zjednodusena = slozka / 'uroven2.json'
        for soubor in (slozka / 'uroven1.json', zjednodusena):
            if soubor.exists():
                jine = sum(1 for f in json.loads(soubor.read_text(encoding='utf-8'))['features']
                           if not je_latinka((f.get('properties') or {}).get('nazev') or ''))
                if jine:
                    jine_pismo[iso3] = jine_pismo.get(iso3, 0) + jine
        if zmeny:
            celkem += sum(zmeny.values())
            zprava.append(f'- {iso3}: ' + ', '.join(f'{k} {v}' for k, v in zmeny.items())
                          + (f' (např. {"; ".join(priklady)})' if priklady else ''))
            log(f'{iso3}: {zmeny}')
    zprava += ['', f'Celkem změněno {celkem} názvů.', '',
               '## Zbývá v jiném písmu', '',
               'Tyto názvy převede až nové generování státu (doplní se anglický název z OSM):', '']
    zprava += [f'- {k}: {v}' for k, v in sorted(jine_pismo.items())] or ['- nic']
    ZPRAVY.mkdir(exist_ok=True)
    (ZPRAVY / 'nazvy.md').write_text('\n'.join(zprava) + '\n', encoding='utf-8')


def prepis_vrstvu(iso3, klic, uprav):
    slozka = DATA / 'svet' / iso3
    meta_soubor = slozka / 'meta.json'
    if not meta_soubor.exists() or not (slozka / f'{klic}.json').exists():
        return None
    meta = json.loads(meta_soubor.read_text(encoding='utf-8'))
    prvky = nacti_plnou(slozka, klic)
    nove, popis = uprav(prvky)
    if popis:
        meta[klic] = {**meta[klic], **uloz_vrstvu(slozka, klic, nove, LIMIT_PLNE_SVET)}
        meta_soubor.write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
    return popis


def prevest(z, do, oblast, nazev):
    radky = []
    jadro = oblast.buffer(-0.001)

    def odebrat(prvky):
        nove = []; zmeny = 0
        for p in prvky:
            if jadro.contains(p['geom'].representative_point()):
                zmeny += 1
                continue
            if p['geom'].intersects(jadro):
                p = {**p, 'geom': jen_plochy(p['geom'].difference(oblast))}
                zmeny += 1
            nove.append(p)
        return nove, f'odebráno nebo oříznuto {zmeny} prvků' if zmeny else ''

    def pridat_obrys(prvky):
        if not prvky:
            return prvky, ''
        g = prvky[0]['geom']
        if g.contains(jadro.representative_point()) and g.intersection(oblast).area > oblast.area * 0.95:
            return prvky, ''
        return [{**prvky[0], 'geom': jen_plochy(unary_union([g, oblast]))}], 'oblast přidána k obrysu'

    def pridat_celek(prvky):
        pokryto = unary_union([p['geom'] for p in prvky]).intersection(oblast).area if prvky else 0
        if pokryto > oblast.area * 0.9:
            return prvky, ''
        return prvky + [{'vlastnosti': {'id': '', 'nazev': nazev}, 'geom': oblast}], f'přidán celek „{nazev}“'

    for klic in ('stat', 'uroven1', 'uroven2'):
        p = prepis_vrstvu(z, klic, odebrat)
        if p:
            radky.append(f'  - {z} {klic}: {p}')
        p = prepis_vrstvu(do, klic, pridat_obrys if klic == 'stat' else pridat_celek)
        if p:
            radky.append(f'  - {do} {klic}: {p}')
    return radky or ['  - data už odpovídají pravidlu']


def rovnobezka(pr):
    jih = box(-180, -90, 180, pr['sirka'])
    sever = box(-180, pr['sirka'], 180, 90)
    slozka = DATA / 'svet' / pr['stat']
    if not (slozka / 'stat.json').exists():
        return ['- stát není v databázi']
    obrys = nacti_plnou(slozka, 'stat')
    g = unary_union([p['geom'] for p in obrys])
    novy = DATA / 'svet' / pr['novy']
    stary_novy = unary_union([p['geom'] for p in nacti_plnou(novy, 'stat')]) if (novy / 'stat.json').exists() else Polygon()
    uzemi = jen_plochy(unary_union([g.intersection(jih), stary_novy]))
    radky = []
    if uzemi.is_empty or uzemi.area < 1:
        radky.append('- jižně od rovnoběžky stát v OSM nic nemá; Západní Sahara je v OSM zvlášť nebo chybí – zkontrolovat ručně')
    else:
        meta = {'vytvoreno': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
                'stat': {'nazev': 'Obrys území', 'skupina': 'Správní členění', **uloz_vrstvu(novy, 'stat', [{'vlastnosti': {'id': pr['novy'], 'nazev': pr['nazev_noveho']}, 'geom': uzemi}])}}
        (novy / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
        radky.append(f'- vytvořeno území {pr["nazev_noveho"]}')

    def oriznout(prvky):
        nove = []; zmeny = 0
        for p in prvky:
            if p['geom'].intersects(jih):
                zmeny += 1
                p = {**p, 'geom': jen_plochy(p['geom'].intersection(sever))}
                if p['geom'].is_empty:
                    continue
            nove.append(p)
        return nove, f'oříznuto {zmeny} prvků po {pr["sirka"]}° s. š.' if zmeny else ''
    for klic in ('stat', 'uroven1', 'uroven2'):
        p = prepis_vrstvu(pr['stat'], klic, oriznout)
        if p:
            radky.append(f'- {pr["stat"]} {klic}: {p}')
    return radky


# ------------------------------------------------------------------ index

def index():
    staty = {s['id']: s for s in staty_editoru()}
    uzemi = []
    cz = DATA / 'CZE' / 'meta.json'
    if cz.exists():
        m = json.loads(cz.read_text(encoding='utf-8'))
        uzemi.append({'kod': 'CZE', 'nazev': 'Česko', 'hledat': 'Czechia Ceska republika', 'pozn': 'nejpodrobnější',
                      'vrstvy': [{'klic': k, **v} for k, v in m['vrstvy'].items()]})
    svet_meta = DATA / 'svet' / 'meta.json'
    if svet_meta.exists():
        m = json.loads(svet_meta.read_text(encoding='utf-8'))
        uzemi.append({'kod': 'SVET', 'nazev': 'Svět – všechny státy', 'vrstvy': [{'klic': k, **v} for k, v in m.items() if not k.startswith('_')]})
    zeme = []
    for slozka in sorted((DATA / 'svet').glob('*/')) if (DATA / 'svet').exists() else []:
        mf = slozka / 'meta.json'
        if not mf.exists():
            continue
        m = json.loads(mf.read_text(encoding='utf-8'))
        if slozka.name == 'CZE' and uzemi and uzemi[0]['kod'] == 'CZE':
            # Česko má vlastní podrobné vrstvy – ze světa jen obrys státu.
            if 'stat' in m:
                uzemi[0]['vrstvy'].insert(0, {'klic': 'stat', **m['stat']})
            continue
        s = staty.get(slozka.name, {'nazev': slozka.name, 'en': ''})
        vrstvy = [{'klic': k, **v} for k, v in m.items() if not k.startswith('_') and isinstance(v, dict) and 'soubory' in v]
        nazev = s['nazev']
        zeme.append({'kod': slozka.name, 'nazev': nazev, 'hledat': s.get('en', ''),
                     'pozn': '1. a 2. úroveň' if len(vrstvy) > 2 else '1. úroveň' if len(vrstvy) == 2 else '', 'vrstvy': vrstvy})
    zeme.sort(key=lambda x: x['nazev'])
    uzemi += zeme
    vysledek = {
        'verze': 1,
        'vytvoreno': dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds'),
        'licence': 'Data © přispěvatelé OpenStreetMap, licence ODbL (https://www.openstreetmap.org/copyright). Sporná území upravená podle pravidel redakce ČT.',
        'uzemi': uzemi,
    }
    (KOREN / 'index.json').write_text(json.dumps(vysledek, ensure_ascii=False, indent=1), encoding='utf-8')
    celkem = sum(f.stat().st_size for f in KOREN.rglob('*') if f.is_file() and '.git' not in f.parts)
    chyby = DATA / 'svet' / 'chyby.json'
    ZPRAVY.mkdir(exist_ok=True)
    (ZPRAVY / 'souhrn.md').write_text('\n'.join([
        '# Souhrn databáze', '',
        f'Index vytvořen {vysledek["vytvoreno"]}. Území: {len(uzemi)}. Velikost všech souborů: {celkem / 1024 / 1024:.0f} MB (limit GitHub Pages je 1 GB).', '',
        '| území | vrstvy |', '|---|---|',
        *[f'| {u["nazev"]} | {", ".join(f"{v["nazev"]} ({v["prvku"]})" + ("" if v["soubory"].get("plna") else " – jen zjednodušená") for v in u["vrstvy"])} |' for u in uzemi],
        '', '## Chyby při stahování', '',
        *([f'- {k}: {v}' for k, v in json.loads(chyby.read_text(encoding="utf-8")).items()] if chyby.exists() else ['žádné']),
    ]) + '\n', encoding='utf-8')
    log(f'index.json: {len(uzemi)} území, {celkem / 1024 / 1024:.0f} MB')


# ------------------------------------------------------------------ spuštění

def main():
    a = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    a.add_argument('co', choices=['pruzkum', 'cesko', 'svet', 'svet-znovu', 'svet-plne', 'svet-vybrane', 'sporna', 'nazvy', 'zmensit', 'index', 'vse'])
    a.add_argument('--minut', type=float, default=300, help='časový limit pro svět')
    a.add_argument('--staty', default='', help='pro svet-vybrane: kódy států oddělené čárkou nebo mezerou (např. PRT,MKD)')
    arg = a.parse_args()
    PRACOVNI.mkdir(parents=True, exist_ok=True)
    if arg.co == 'pruzkum':
        pruzkum()
    if arg.co in ('cesko', 'vse'):
        cesko()
    if arg.co in ('svet', 'svet-znovu', 'svet-plne', 'svet-vybrane', 'vse'):
        vybrane = [k.strip().upper() for k in re.split(r'[,\s]+', arg.staty) if k.strip()] if arg.co == 'svet-vybrane' else []
        if arg.co == 'svet-vybrane' and not vybrane:
            a.error('svet-vybrane potřebuje --staty')
        svet(arg.minut, znovu=arg.co == 'svet-znovu', doplnit_plne=arg.co == 'svet-plne', vybrane=vybrane)
    if arg.co == 'zmensit':
        zmensi_svet()
    if arg.co == 'nazvy':
        nazvy()
    if arg.co in ('sporna', 'vse'):
        sporna()
    if arg.co != 'pruzkum':
        index()


if __name__ == '__main__':
    sys.exit(main())
