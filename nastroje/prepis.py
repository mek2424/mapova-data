"""Česká transkripce z cyrilice podle Internetové jazykové příručky ÚJČ AV ČR.

Tabulky: ruština (prirucka.ujc.cas.cz/?id=924), ukrajinština (926), běloruština (921),
bulharština (922), makedonština (923), srbština (925). Kde příručka dává na výběr, platí:
- běloruské ў → ŭ (příručka: „Ŭ ŭ nebo U u“ ve všech pozicích),
- bulharské ъ → ă (příručka: „Ă ă | A a“),
- srbské ђ → đ, ћ → ć (příručka: „Dj dj | Đ đ“, doporučuje ć),
- makedonský apostrof (šva) se zachovává,
- ruské е po б, в, м, п jen jako e (varianta ě je volitelná a závisí na významu slova),
- ruské кс vždy jako ks (x jen ve slovech latinského a řeckého původu – nelze poznat strojově).

prepis(text, jazyk) vrací přepsaný text, nebo None, když text obsahuje cyrilská písmena,
která do abecedy daného jazyka nepatří (např. kazašské ә, қ) – takový název přepis nezná.
"""
import re

CYRILICE = re.compile('[Ѐ-ӿ]')
APOSTROFY = "'’ʼ"

SAMOHLASKY = {
    'ru': set('аеёиоуыэюя'),
    'uk': set('аеєиіїоуюя'),
    'be': set('аеёіоуыэюя'),
    'bg': set('аеиѝоуъюя'),
    'mk': set('аеѐиѝоу'),
    'sr': set('аеиоу'),
}

ABECEDA = {
    'ru': set('абвгдеёжзийклмнопрстуфхцчшщъыьэюя'),
    'uk': set('абвгґдеєжзиіїйклмнопрстуфхцчшщьюя'),
    'be': set('абвгґдеёжзійклмнопрстуўфхцчшыьэюя'),
    'bg': set('абвгдежзийѝклмнопрстуфхцчшщъьюя'),
    'mk': set('абвгдѓеѐжзѕиѝјклљмнњопрстќуфхцчџш'),
    'sr': set('абвгдђежзијклљмнњопрстћуфхцчџш'),
}

ZAKLAD = {
    'ru': dict(а='a', б='b', в='v', г='g', д='d', ж='ž', з='z', и='i', й='j', к='k', л='l', м='m', н='n',
               о='o', п='p', р='r', с='s', т='t', у='u', ф='f', х='ch', ц='c', ч='č', ш='š', щ='šč',
               ъ='', ы='y', ь='', э='e'),
    'uk': dict(а='a', б='b', в='v', г='h', ґ='g', д='d', е='e', ж='ž', з='z', и='y', і='i', ї='ji', й='j',
               к='k', л='l', м='m', н='n', о='o', п='p', р='r', с='s', т='t', у='u', ф='f', х='ch', ц='c',
               ч='č', ш='š', щ='šč', ь=''),
    'be': dict(а='a', б='b', в='v', г='h', ґ='g', д='d', ж='ž', з='z', й='j', к='k', л='l', м='m', н='n',
               о='o', п='p', р='r', с='s', т='t', у='u', ў='ŭ', ф='f', х='ch', ц='c', ч='č', ш='š', ы='y',
               ь='', э='e'),
    'bg': dict(а='a', б='b', в='v', г='g', д='d', е='e', ж='ž', з='z', и='i', ѝ='í', й='j', к='k', л='l',
               м='m', н='n', о='o', п='p', р='r', с='s', т='t', у='u', ф='f', х='ch', ц='c', ч='č', ш='š',
               щ='št', ъ='ă'),
    'mk': dict(а='a', б='b', в='v', г='g', д='d', ѐ='é', ж='ž', з='z', ѕ='dz', и='i', ѝ='í', ј='j', к='k',
               л='l', љ='lj', м='m', н='n', о='o', п='p', р='r', с='s', т='t', у='u', ф='f', х='ch', ц='c',
               ч='č', џ='dž', ш='š'),
    'sr': dict(а='a', б='b', в='v', г='g', д='d', ђ='đ', е='e', ж='ž', з='z', и='i', ј='j', к='k', л='l',
               љ='lj', м='m', н='n', њ='nj', о='o', п='p', р='r', с='s', т='t', ћ='ć', у='u', ф='f', х='h',
               ц='c', ч='č', џ='dž', ш='š'),
}

MEKKE = {'д': 'ď', 'т': 'ť', 'н': 'ň'}
TVRDE = {'д': 'd', 'т': 't', 'н': 'n'}

# Kód státu (ISO 3166-1 alpha-3) → jazyk přepisu. Jiné jazyky s cyrilicí (kazaština, kyrgyzština,
# tádžičtina, mongolština…) příručka nepokrývá – u nich se přepisuje jen ruský název z OSM (name:ru).
JAZYK_STATU = {'RUS': 'ru', 'UKR': 'uk', 'BLR': 'be', 'BGR': 'bg', 'MKD': 'mk',
               'SRB': 'sr', 'MNE': 'sr', 'BIH': 'sr', 'XKX': 'sr'}


def jazyk_statu(iso3):
    return JAZYK_STATU.get(iso3)


def ma_cyrilici(text):
    return bool(text and CYRILICE.search(text))


def _velikost(vzor, i, text, vystup):
    """Velké písmeno z cyrilice → velké v přepisu (dvojznak „Ch“, celé velkými „CH“)."""
    if not vystup or not vzor[i].isupper():
        return vystup
    dalsi = vzor[i + 1] if i + 1 < len(vzor) else ''
    predchozi = vzor[i - 1] if i > 0 else ''
    if len(vystup) > 1 and (dalsi.isupper() or (predchozi.isupper() and not dalsi.isalpha())):
        return vystup.upper()
    return vystup[0].upper() + vystup[1:]


def prepis(text, jazyk):
    if not ma_cyrilici(text):
        return text
    abeceda = ABECEDA[jazyk]
    if any(CYRILICE.match(z) and z.lower() not in abeceda for z in text):
        return None
    s = text
    m = s.lower()
    samohl = SAMOHLASKY[jazyk]
    zaklad = ZAKLAD[jazyk]
    out = []
    n = len(m)

    def z(k):
        # Mimo text vrací znak, který nepatří do žádné množiny ('' by v Pythonu „bylo“ v každém řetězci).
        return m[k] if 0 <= k < n else '\0'

    def zacatek(k):
        p = z(k - 1)
        return not (CYRILICE.match(p) or p in APOSTROFY)

    for i, c in enumerate(m):
        p, d, d2 = z(i - 1), z(i + 1), z(i + 2)
        if not CYRILICE.match(c):
            if c in APOSTROFY and jazyk in ('uk', 'be') and CYRILICE.match(p) and CYRILICE.match(d):
                out.append('')       # ukrajinský a běloruský apostrof se vynechává
            else:
                out.append(s[i])
            continue
        r = None
        if jazyk == 'ru':
            jotace = zacatek(i) or p in samohl or p in 'ьъ'
            if c in 'дтн':
                r = MEKKE[c] if d in 'ёюяь' else TVRDE[c]
            elif c == 'е':
                r = 'je' if jotace else ('ě' if p in 'дтн' else 'e')
            elif c == 'ё':
                r = 'jo' if jotace else ('o' if p in 'жчшщдтн' else 'jo')
            elif c in 'юя':
                v = 'u' if c == 'ю' else 'a'
                r = 'j' + v if jotace else (v if p in 'дтн' else 'j' + v)
            elif c == 'и':
                r = 'ji' if p == 'ь' else 'i'
            elif c == 'о':
                r = 'jo' if p == 'ь' else 'o'
        elif jazyk == 'uk':
            jotace = zacatek(i) or p in samohl or p in APOSTROFY or p == 'ь'
            if c in 'дтн':
                mekci = d in 'юяь' or (d == c and d2 in 'юяьєі')
                r = MEKKE[c] if mekci else TVRDE[c]
            elif c == 'є':
                r = 'je' if jotace else ('ě' if p in 'дтн' else 'e')
            elif c in 'юя':
                v = 'u' if c == 'ю' else 'a'
                r = 'j' + v if jotace else (v if p in 'дтн' else 'j' + v)
        elif jazyk == 'be':
            jotace = zacatek(i) or p in samohl or p in APOSTROFY or p in 'ўь'
            if c == 'н':
                mekci = d in 'ёюяь' or (d == 'н' and d2 in 'еёюяіь')
                r = 'ň' if mekci else 'n'
            elif c == 'е':
                r = 'je' if jotace else ('ě' if p == 'н' else 'e')
            elif c == 'ё':
                r = 'jo' if jotace else ('o' if p == 'н' else 'jo')
            elif c == 'і':
                r = 'ji' if (p in samohl or p in APOSTROFY or p in 'ўь') and not zacatek(i) else 'i'
            elif c in 'юя':
                v = 'u' if c == 'ю' else 'a'
                r = 'j' + v if jotace else (v if p == 'н' else 'j' + v)
        elif jazyk == 'bg':
            if c in 'дтн':
                r = MEKKE[c] if d in 'юяь' else TVRDE[c]
            elif c == 'ь':
                r = '' if p in 'дтн' else 'j'
            elif c in 'юя':
                v = 'u' if c == 'ю' else 'a'
                r = v if p in 'дтн' else 'j' + v
        elif jazyk == 'mk':
            if c in 'ѓќњ':
                r = {'ѓ': 'd', 'ќ': 't', 'њ': 'n'}[c] if d in 'еи' else {'ѓ': 'ď', 'ќ': 'ť', 'њ': 'ň'}[c]
            elif c == 'е':
                r = 'ě' if p in 'ѓќњ' else 'e'
        if r is None:
            r = zaklad.get(c, s[i])
        out.append(_velikost(s, i, s, r))
    return ''.join(out)
