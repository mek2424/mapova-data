"""Kontrola přepisu na příkladech z Internetové jazykové příručky ÚJČ. Spuštění: python nastroje/test_prepis.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from prepis import prepis  # noqa: E402

PRIKLADY = {
    'ru': {
        'Астрахань': 'Astrachaň', 'Обь': 'Ob', 'Владивосток': 'Vladivostok', 'подъезд': 'podjezd',
        'Надежда': 'Naděžda', 'молодёжь': 'moloďož', 'дюжина': 'ďužina', 'ледяной': 'leďanoj', 'свадьба': 'svaďba',
        'площадь': 'ploščaď', 'Екатеринбург': 'Jekatěrinburg', 'Николаев': 'Nikolajev', 'управление': 'upravlenije',
        'Прокопьевск': 'Prokopjevsk', 'Шереметьево': 'Šeremeťjevo', 'субъект': 'subjekt', 'Архангельск': 'Archangelsk',
        'Ижевск': 'Iževsk', 'Демидов': 'Děmidov', 'Воронеж': 'Voroněž', 'ёлка': 'jolka', 'ребёнок': 'rebjonok',
        'Пётр': 'Pjotr', 'серьёзный': 'serjoznyj', 'чутьё': 'čuťjo', 'жёлтый': 'žoltyj', 'Будённый': 'Buďonnyj',
        'Артём': 'Arťom', 'Нижний Новгород': 'Nižnij Novgorod', 'Марьино': 'Marjino', 'Ильич': 'Iljič',
        'Игнатьич': 'Ignaťjič', 'Йошкар-Ола': 'Joškar-Ola', 'Енисей': 'Jenisej', 'Новокузнецк': 'Novokuzněck',
        'Селезнёв': 'Selezňov', 'Казань': 'Kazaň', 'батальон': 'bataljon', 'компаньон': 'kompaňjon',
        'Тюмень': 'Ťumeň', 'нефть': 'něfť', 'Щёлково': 'Ščolkovo', 'Мытищи': 'Mytišči', 'объяснить': 'objasniť',
        'Тверь': 'Tver', 'Рязань': 'Rjazaň', 'пьеса': 'pjesa', 'угодье': 'ugoďje', 'кладью': 'klaďju',
        'крестьянин': 'kresťjanin', 'колдунья': 'kolduňja', 'Улан-Удэ': 'Ulan-Ude', 'Юрий': 'Jurij',
        'Любов': 'Ljubov', 'июнь': 'ijuň', 'адъютант': 'adjutant', 'дюна': 'ďuna', 'отнюдь': 'otňuď',
        'Якутск': 'Jakutsk', 'Брянск': 'Brjansk', 'передняя': 'peredňaja', 'Ульяновск': 'Uljanovsk',
        'Татьяна': 'Taťjana', 'дятел': 'ďatěl', 'Аня': 'Aňa', 'Чернышевский': 'Černyševskij',
    },
    'uk': {
        'Бахмач': 'Bachmač', 'Вінниця': 'Vinnycja', 'Кривий Ріг': 'Kryvyj Rih', 'Ужгород': 'Užhorod',
        'Ґорґани': 'Gorgany', 'Лебедєве': 'Lebeděve', 'Гадяч': 'Haďač', 'Бершадь': 'Beršaď', 'суддя': 'suďďa',
        'суддівський': 'suďdivskyj', 'Тернопіль': 'Ternopil', 'Єнакієве': 'Jenakijeve', 'Докучаєвськ': 'Dokučajevsk',
        'Тур\'є': 'Turje', 'по-третє': 'po-tretě', 'Середнє': 'Seredně', 'суттєвий': 'suťtěvyj', 'останнє': 'ostaňně',
        'Сєдове': 'Sedove', 'Миколаїв': 'Mykolajiv', 'Іллінці': 'Illinci', 'Ірпінь': 'Irpiň', 'Їжівці': 'Jiživci',
        'Ізмаїл': 'Izmajil', 'Київ': 'Kyjiv', 'Ананьїв': 'Anaňjiv', 'Мар\'їнка': 'Marjinka', 'Кропивницький': 'Kropyvnyckyj',
        'Верхнє Водяне': 'Verchně Voďane', 'Снятин': 'Sňatyn', 'Коростень': 'Korosteň', 'питання': 'pytaňňa',
        'безсторонньо': 'bezstoroňňo', 'останній': 'ostaňnij', 'Третє': 'Tretě', 'Гнатюк': 'Hnaťuk',
        'Прип\'ять': 'Prypjať', 'Закарпаття': 'Zakarpaťťa', 'життьовий': 'žyťťovyj', 'на Закарпатті': 'na Zakarpaťti',
        'Україна': 'Ukrajina', 'Херсон': 'Cherson', 'Біла Церква': 'Bila Cerkva', 'Городище': 'Horodyšče',
        'Львів': 'Lviv', 'Луцьк': 'Luck', 'Усть-Чорна': 'Usť-Čorna', 'Батьово': 'Baťovo', 'Южне': 'Južne',
        'Андріюк': 'Andrijuk', 'Ізюм': 'Izjum', 'Стеф\'юк': 'Stefjuk', 'Костюк': 'Kosťuk', 'статтю': 'staťťu',
        'Яворів': 'Javoriv', 'Переяслав': 'Perejaslav', 'Олександрія': 'Oleksandrija', 'мільярд': 'miljard',
        'П\'ятихатки': 'Pjatychatky', 'Бердянськ': 'Berďansk', 'Тячів': 'Ťačiv', 'Щастя': 'Ščasťa',
        'століття': 'stoliťťa', 'Кам\'янець-Подільський': 'Kamjanec-Podilskyj', 'від\'їзд': 'vidjizd',
        'Запоріжжя': 'Zaporižžja', 'Чернівці': 'Černivci', 'Дніпро': 'Dnipro',
    },
    'be': {
        'Ашмяны': 'Ašmjany', 'Брэст': 'Brest', 'Гомель': 'Homel', 'Дуброўна': 'Dubroŭna', 'Дзмітрый': 'Dzmitryj',
        'Генадзь': 'Henadz', 'Ельск': 'Jelsk', 'Круглае': 'Kruhlaje', 'Заслаўе': 'Zaslaŭje', 'льеш': 'lješ',
        'б\'еш': 'bješ', 'Вянера': 'Vjaněra', 'навучанне': 'navučaňně', 'Беларусь': 'Belarus', 'Глеб': 'Hleb',
        'раён': 'rajon', 'мільён': 'miljon', 'Магілёў': 'Mahiljoŭ', 'анёл': 'aňol', 'свіннёю': 'sviňňoju',
        'краіна': 'krajina', 'здароўі': 'zdaroŭji', 'Ільінскі': 'Iljinski', 'Мар\'іна Горка': 'Marjina Horka',
        'Мікалай': 'Mikalaj', 'Каменец': 'Kameněc', 'Нясвіж': 'Ňasviž', 'Смаргонь': 'Smarhoň', 'свінні': 'sviňni',
        'Барысаў': 'Barysaŭ', 'Хойнікі': 'Chojniki', 'Таццяна': 'Taccjana', 'Мазыр': 'Mazyr', 'Васіль': 'Vasil',
        'Чэрыкаў': 'Čerykaŭ', 'Юрый': 'Juryj', 'Людміла': 'Ljudmila', 'Танюся': 'Taňusja', 'восенню': 'voseňňu',
        'Ян': 'Jan', 'заходняя': 'zachodňaja', 'Кацярына': 'Kacjaryna', 'Ілья': 'Ilja', 'Емяльян': 'Jemjaljan',
        'Венядзікт': 'Veňadzikt', 'Ефрасіння': 'Jefrasiňňa', 'Дар\'я': 'Darja', 'ад\'ехаць': 'adjechac',
        'Віцебск': 'Vicebsk', 'Маладзечна': 'Maladzečna', 'Шчучын': 'Ščučyn',
    },
    'bg': {
        'Асеновград': 'Asenovgrad', 'Адрияна': 'Adrijana', 'Девня': 'Devňa', 'Гроздьово': 'Grozďovo',
        'Дюля': 'Ďulja', 'Николаево': 'Nikolaevo', 'Благоевград': 'Blagoevgrad', 'Съединение': 'Săedinenie',
        'Пазарджик': 'Pazardžik', 'Коньовец': 'Koňovec', 'Гняздово': 'Gňazdovo', 'Зовтьово': 'Zovťovo',
        'Тянево': 'Ťanevo', 'Свищов': 'Svištov', 'Несебър': 'Nesebăr', 'Кърджали': 'Kărdžali',
        'Велико Търново': 'Veliko Tărnovo', 'Гьоврен': 'Gjovren', 'Бакьово': 'Bakjovo', 'Бельово': 'Beljovo',
        'Панагюрище': 'Panagjurište', 'Кюстендил': 'Kjustendil', 'Тюленово': 'Ťulenovo', 'Ямбол': 'Jambol',
        'Смолян': 'Smoljan', 'Оряхово': 'Orjachovo', 'Петя': 'Peťa', 'Огнян': 'Ogňan', 'Стара Загора': 'Stara Zagora',
    },
    'mk': {
        'Анастасија': 'Anastasija', 'Ѓорче': 'Ďorče', 'Воѓани': 'Voďani', 'Долно Ѓуѓанци': 'Dolno Ďuďanci',
        'Ѓурѓевиште': 'Ďurděvište', 'Ѓурѓица': 'Ďurdica', 'Калуѓерица': 'Kaluděrica', 'Плаќе': 'Platě',
        'Радање': 'Radaně', 'Ѕвонко': 'Dzvonko', 'Скопје': 'Skopje', 'Љупка': 'Ljupka', 'Коњско': 'Koňsko',
        'Пчиња': 'Pčiňa', 'Бибање': 'Bibaně', 'Свињиште': 'Svinište', 'Ќафа': 'Ťafa', 'Глишиќ': 'Glišiť',
        'Еднокуќево': 'Ednokutěvo', 'Ќирил': 'Tiril', 'Охрид': 'Ochrid', 'Џумабос': 'Džumabos', 'Штип': 'Štip',
        'Општина Аеродром': 'Opština Aerodrom', 'Демир Хисар/Мургашево': 'Demir Chisar/Murgaševo',
    },
    'sr': {
        'Ваљево': 'Valjevo', 'Ђурђево': 'Đurđevo', 'Карађорђевић': 'Karađorđević', 'Зрењанин': 'Zrenjanin',
        'Љиљана': 'Ljiljana', 'Немања': 'Nemanja', 'Ћуприја': 'Ćuprija', 'Владичин Хан': 'Vladičin Han',
        'Караџић': 'Karadžić', 'Приштина': 'Priština', 'Нови Сад': 'Novi Sad', 'Огњен': 'Ognjen',
    },
}


def main():
    chyby = 0
    pocet = 0
    for jazyk, priklady in PRIKLADY.items():
        for vstup, cekam in priklady.items():
            pocet += 1
            vysledek = prepis(vstup, jazyk)
            if vysledek != cekam:
                chyby += 1
                print(f'{jazyk}: {vstup} → {vysledek}, čekáno {cekam}')
    assert prepis('Әлмати', 'ru') is None
    assert prepis('Praha', 'ru') == 'Praha'
    assert prepis('МОСКВА', 'ru') == 'MOSKVA' and prepis('ХАРКІВ', 'uk') == 'CHARKIV'
    print(f'{pocet - chyby} z {pocet} příkladů v pořádku')
    return 1 if chyby else 0


if __name__ == '__main__':
    sys.exit(main())
