#!/usr/bin/env python3
"""Generátor databáze GeoJSONů z OpenStreetMap pro Mapový editor (mek2424/mapovy-editor).

Spouští se v GitHub Actions (.github/workflows/data.yml), kde je přístup k Geofabriku a Overpassu.

  python nastroje/generuj.py pruzkum   – zjistí, jak jsou hranice v OSM označené (zpravy/*.md)
  python nastroje/generuj.py cesko     – vrstvy Česka podle nastroje/vrstvy-cesko.json
  python nastroje/generuj.py svet      – státy + 1. a 2. úroveň správních celků (navazuje, kde skončil)
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
MAPSHAPER = ['mapshaper'] if shutil.which('mapshaper') else ['npx', '-y', 'mapshaper@0.7.90']

# Zjednodušená verze: kolik bodů zůstane (mapshaper -simplify) a přesnost souřadnic.
ZJEDNODUSENI = '6%'
PRESNOST_ZJEDNODUSENE = '0.00001'   # ~1 m
PRESNOST_PLNE = '0.000001'          # ~10 cm
LIMIT_SOUBORU = 95 * 1024 * 1024    # GitHub nepřijme soubor nad 100 MB
# GitHub Pages mají limit 1 GB na celý web – plné verze ve světě jen do této velikosti.
LIMIT_PLNE_SVET = int(os.environ.get('LIMIT_PLNE_SVET_MB', '25')) * 1024 * 1024


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


def cesky_nazev(tagy):
    return tagy.get('name:cs') or tagy.get('name') or tagy.get('name:en') or ''


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
    mapshaper('-i', str(surovy), 'encoding=utf8', '-o', str(plna), 'format=topojson', 'no-quantization', f'precision={PRESNOST_PLNE}')
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


def najdi_oblast(jmena):
    """Relace sporné oblasti v OSM podle name:en. Vrací (geometrie, popis nalezeného) nebo (None, důvod)."""
    vyrazy = '|'.join(re.escape(j) for j in jmena)
    dotaz = f'[out:xml][timeout:600];rel["name:en"~"^({vyrazy})$"]["boundary"];(._;>;);out body;'
    cil = PRACOVNI / f'oblast-{re.sub(r"[^a-z]", "", jmena[0].lower())}.osm'
    cil.parent.mkdir(parents=True, exist_ok=True)
    overpass(dotaz, cil)
    nalezene = plochy(cil)
    if not nalezene:
        return None, 'v OSM nenalezeno'
    # Více relací (např. ukrajinská i ruská správa Krymu): spojit, ať je pokrytá celá oblast.
    popis = '; '.join(f"{p['osm']} {p['tagy'].get('name', '')} (boundary={p['tagy'].get('boundary')}, admin_level={p['tagy'].get('admin_level', '-')})" for p in nalezene)
    return unary_union([p['geom'] for p in nalezene]), popis


def pruzkum_sporna():
    with open(NASTROJE / 'sporna-uzemi.json', encoding='utf-8') as f:
        pravidla = json.load(f)['pravidla']
    radky = ['# Průzkum sporných území v OSM', '', 'Které relace OSM se pro pravidla z nastroje/sporna-uzemi.json najdou a do kterých států (admin_level=2) v OSM spadají.', '']
    for pr in pravidla:
        radky.append(f'## {pr["nazev"]}')
        for o in pr.get('oblasti', []):
            try:
                g, popis = najdi_oblast(o['name:en'])
            except Exception as e:  # noqa: BLE001
                radky.append(f'- {o["nazev"]}: chyba Overpassu {e}')
                continue
            if g is None:
                radky.append(f'- {o["nazev"]}: {popis}')
                continue
            b = g.representative_point()
            dotaz = f'[out:json][timeout:120];is_in({b.y},{b.x})->.a;rel(pivot.a)["boundary"="administrative"]["admin_level"="2"];out tags;'
            try:
                staty = [x['tags'].get('ISO3166-1:alpha3', x['tags'].get('name')) for x in json.loads(overpass(dotaz))['elements']]
            except Exception as e:  # noqa: BLE001
                staty = [f'chyba {e}']
            radky.append(f'- {o["nazev"]}: {popis}; v OSM leží ve státech {", ".join(staty) or "žádném"}')
        if pr['druh'] == 'rovnobezka':
            radky.append(f'- dělí se stát {pr["stat"]} po {pr["sirka"]}° s. š.')
        radky.append('')
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


def zvol_urovne(pocty, iso3):
    if iso3 in PREPSANE_UROVNE:
        return PREPSANE_UROVNE[iso3]
    urovne = sorted(pocty)
    l1 = next((u for u in urovne if pocty[u] >= 2), None)
    if l1 is None:
        return []
    l2 = next((u for u in urovne if u > l1 and pocty[u] > pocty[l1] and pocty[u] <= 6000), None)
    return [l1] if l2 is None else [l1, l2]


def stat_ze_sveta(s, slozka):
    iso2 = s['iso']
    oblast = f'rel["boundary"="administrative"]["admin_level"="2"]["ISO3166-1"="{iso2}"]'
    soubor = PRACOVNI / f'{s["id"]}-0.osm'
    overpass(f'[out:xml][timeout:900];{oblast};(._;>;);out body;', soubor)
    obrys = [p for p in plochy(soubor) if p['tagy'].get('admin_level') == '2']
    soubor.unlink(missing_ok=True)
    if not obrys:
        raise RuntimeError('obrys státu v OSM nenalezen')
    obrys_g = unary_union([p['geom'] for p in obrys])
    meta = {'stat': {'nazev': 'Obrys státu', 'skupina': 'Správní členění', **uloz_vrstvu(slozka, 'stat', [{'vlastnosti': {'id': s['id'], 'nazev': s['nazev']}, 'geom': obrys_g}], LIMIT_PLNE_SVET)}}

    tagy = json.loads(overpass(f'[out:json][timeout:900];{oblast};map_to_area->.z;rel(area.z)["boundary"="administrative"]["admin_level"~"^([3-9]|10)$"];out tags;'))
    pocty = Counter(int(x['tags']['admin_level']) for x in tagy['elements'] if x['tags'].get('admin_level', '').isdigit())
    urovne = zvol_urovne(pocty, s['id'])
    meta['_urovne'] = {'pocty': dict(sorted(pocty.items())), 'zvolene': urovne}
    predchozi = None
    for i, uroven in enumerate(urovne, start=1):
        soubor = PRACOVNI / f'{s["id"]}-{uroven}.osm'
        overpass(f'[out:xml][timeout:1800][maxsize:4000000000];{oblast};map_to_area->.z;rel(area.z)["boundary"="administrative"]["admin_level"="{uroven}"];(._;>;);out body;', soubor)
        okoli = obrys_g.buffer(0.01)
        prvky = []
        for p in plochy(soubor):
            if p['tagy'].get('admin_level') != str(uroven) or not okoli.contains(p['geom'].representative_point()):
                continue
            kod = p['tagy'].get('ISO3166-2') or p['tagy'].get('ref') or p['osm']
            prvky.append({'vlastnosti': {'id': kod, 'nazev': cesky_nazev(p['tagy'])}, 'geom': p['geom']})
        soubor.unlink(missing_ok=True)
        if i == 2 and predchozi:
            doplnit_nadrazene(prvky, predchozi, 'uroven1')
        typ = Counter(x['tags'].get('border_type', '') for x in tagy['elements'] if x['tags'].get('admin_level') == str(uroven)).most_common(1)
        meta[f'uroven{i}'] = {
            'nazev': f'{i}. úroveň', 'skupina': 'Správní členění',
            'popis': f'admin_level {uroven}' + (f' ({typ[0][0]})' if typ and typ[0][0] else ''),
            **uloz_vrstvu(slozka, f'uroven{i}', prvky, LIMIT_PLNE_SVET),
        }
        predchozi = prvky
    return meta


def zapis_prubeh(radek):
    """Průběh stahování světa – v Actions nejsou záznamy z běhu odjinud vidět."""
    ZPRAVY.mkdir(parents=True, exist_ok=True)
    with open(ZPRAVY / 'prubeh-svet.md', 'a', encoding='utf-8') as f:
        f.write(f'- {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} {radek}\n')


def svet(minut):
    global PREPSANE_UROVNE
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
            continue
        meta['vytvoreno'] = dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
        (slozka / 'meta.json').write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding='utf-8')
        hotovo += 1
        zapis_prubeh(f'{s["id"]} {s["nazev"]}: {time.time() - zacatek:.0f} s, úrovně {meta["_urovne"]["zvolene"]}, '
                     + ', '.join(f'{k} {v["prvku"]}' for k, v in meta.items() if isinstance(v, dict) and 'prvku' in v))
    zbyva = [s['id'] for s in staty if not (DATA / 'svet' / s['id'] / 'meta.json').exists()]
    if not zbyva:
        staty_sveta(staty)
    log(f'Zbývá {len(zbyva)} států.')


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
                g, popis = najdi_oblast(o['name:en'])
                if g is None:
                    zprava.append(f'- {o["nazev"]}: {popis} – beze změny')
                    continue
                zprava.append(f'- {o["nazev"]}: {popis}')
                zprava += prevest(pr['z'], pr['do'], g, o['nazev'])
        elif pr['druh'] == 'rovnobezka':
            zprava += rovnobezka(pr)
        zprava.append('')
    if (DATA / 'svet' / 'meta.json').exists():
        staty_sveta(jedinecne_staty())
    ZPRAVY.mkdir(exist_ok=True)
    (ZPRAVY / 'sporna-uzemi.md').write_text('\n'.join(zprava) + '\n', encoding='utf-8')


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
    a.add_argument('co', choices=['pruzkum', 'cesko', 'svet', 'sporna', 'index', 'vse'])
    a.add_argument('--minut', type=float, default=300, help='časový limit pro svět')
    arg = a.parse_args()
    PRACOVNI.mkdir(parents=True, exist_ok=True)
    if arg.co == 'pruzkum':
        pruzkum()
    if arg.co in ('cesko', 'vse'):
        cesko()
    if arg.co in ('svet', 'vse'):
        svet(arg.minut)
    if arg.co in ('sporna', 'vse'):
        sporna()
    if arg.co != 'pruzkum':
        index()


if __name__ == '__main__':
    sys.exit(main())
