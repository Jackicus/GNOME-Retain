#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
# SPDX-FileCopyrightText: 2026 Jack Tully

"""Build an invented collection with months of study behind it, for screenshots, `--demo`
and the tests.

    scripts/demo_collection.py                       # build/demo
    scripts/demo_collection.py --data-dir DIR --seed 1 --small

Nothing in it is real: the decks (Spanish, Geography, Biology, Japanese, Programming) hold
common words, world capitals, textbook facts and language rules, and the anatomy diagrams are
drawn here with cairo. The history is simulated: an invented clock starts months ago (200
days; 30 with `--small`, which also keeps only half the notes), the learner sits down most
days at some hour between 8:00 and 22:00, Spanish daily and the other decks every other day,
skips about a sixth of the days and takes one longer break, adds notes in batches along the
way, and answers each card by drawing against a hidden memory (FSRS's retrievability shifted
by a per-card aptitude), so the review log has thousands of rows, intervals are spread from
young to mature, a few cards lapse, and today's queue is left for the user: Spanish is kept
at twenty or more due reviews plus new cards (a batch added last night).

The directory is recreated on every run (deleted only when it is empty or holds a
collection.sqlite, as a guard against a wrong --data-dir); the same seed gives the same
collection, apart from the dates, which end yesterday. The collection's clock (ids, creation
times, the review log) follows the invented one.
"""

import argparse
import importlib.util
import io
import math
import pathlib
import random
import shutil
import sys
import time

import cairo

ROOT = pathlib.Path(__file__).resolve().parent.parent
SRC = ROOT / 'src'

if 'retain' not in sys.modules:
    _spec = importlib.util.spec_from_file_location(
        'retain', SRC / '__init__.py', submodule_search_locations=[str(SRC)])
    _module = importlib.util.module_from_spec(_spec)
    sys.modules['retain'] = _module
    _spec.loader.exec_module(_module)

from retain import collection as collection_module  # noqa: E402
from retain import days, deck_config, notetypes  # noqa: E402
from retain.collection import Collection  # noqa: E402
from retain.fsrs import AGAIN, EASY, GOOD, HARD  # noqa: E402
from retain.scheduler import Scheduler  # noqa: E402

HISTORY_DAYS = 200
SMALL_HISTORY_DAYS = 30
BREAK_DAYS = 10
SMALL_BREAK_DAYS = 3
SKIP_CHANCE = 0.15
SPANISH_DUE_MINIMUM = 20

# --- Content -----------------------------------------------------------------------------

# (Spanish, English, tags)
VOCABULARY = [
    ('el pan', 'bread', 'noun food'), ('la manzana', 'apple', 'noun food'),
    ('el queso', 'cheese', 'noun food'), ('la leche', 'milk', 'noun food'),
    ('el huevo', 'egg', 'noun food'), ('el arroz', 'rice', 'noun food'),
    ('el pollo', 'chicken', 'noun food'), ('el pescado', 'fish', 'noun food'),
    ('la sal', 'salt', 'noun food'), ('el azúcar', 'sugar', 'noun food'),
    ('la naranja', 'orange', 'noun food'), ('el vino', 'wine', 'noun food'),
    ('el agua', 'water', 'noun food'), ('la cena', 'dinner', 'noun food'),
    ('el desayuno', 'breakfast', 'noun food'), ('la cuchara', 'spoon', 'noun food'),
    ('el tenedor', 'fork', 'noun food'), ('la cuenta', 'the bill', 'noun food travel'),
    ('el tren', 'train', 'noun travel'), ('el aeropuerto', 'airport', 'noun travel'),
    ('el billete', 'ticket', 'noun travel'), ('la maleta', 'suitcase', 'noun travel'),
    ('la estación', 'station', 'noun travel'), ('el mapa', 'map', 'noun travel'),
    ('la playa', 'beach', 'noun travel'), ('la habitación', 'room', 'noun travel'),
    ('la calle', 'street', 'noun travel'), ('el puente', 'bridge', 'noun travel'),
    ('la ciudad', 'city', 'noun travel'), ('el pueblo', 'village', 'noun travel'),
    ('la frontera', 'border', 'noun travel'), ('el pasaporte', 'passport', 'noun travel'),
    ('la salida', 'exit', 'noun travel'), ('la entrada', 'entrance', 'noun travel'),
    ('la casa', 'house', 'noun home'), ('la cocina', 'kitchen', 'noun home'),
    ('la ventana', 'window', 'noun home'), ('la puerta', 'door', 'noun home'),
    ('la mesa', 'table', 'noun home'), ('la silla', 'chair', 'noun home'),
    ('la cama', 'bed', 'noun home'), ('la llave', 'key', 'noun home'),
    ('el libro', 'book', 'noun home'), ('el reloj', 'clock', 'noun home'),
    ('la luz', 'light', 'noun home'), ('el amigo', 'friend', 'noun people'),
    ('la familia', 'family', 'noun people'), ('el hermano', 'brother', 'noun people'),
    ('la hermana', 'sister', 'noun people'), ('el niño', 'child', 'noun people'),
    ('el trabajo', 'work', 'noun'), ('la semana', 'week', 'noun time'),
    ('el mes', 'month', 'noun time'), ('el año', 'year', 'noun time'),
    ('la mañana', 'morning', 'noun time'), ('la noche', 'night', 'noun time'),
    ('la tarde', 'afternoon', 'noun time'), ('el tiempo', 'weather; time', 'noun'),
    ('el cielo', 'sky', 'noun nature'), ('la lluvia', 'rain', 'noun nature'),
    ('el bosque', 'forest', 'noun nature'), ('el río', 'river', 'noun nature'),
    ('la montaña', 'mountain', 'noun nature'), ('el perro', 'dog', 'noun animal'),
    ('el gato', 'cat', 'noun animal'), ('el pájaro', 'bird', 'noun animal'),
    ('el caballo', 'horse', 'noun animal'),
    ('hablar', 'to speak', 'verb'), ('comer', 'to eat', 'verb food'),
    ('beber', 'to drink', 'verb food'), ('vivir', 'to live', 'verb'),
    ('ir', 'to go', 'verb travel'), ('venir', 'to come', 'verb'),
    ('tener', 'to have', 'verb'), ('hacer', 'to do; to make', 'verb'),
    ('poder', 'to be able to', 'verb'), ('querer', 'to want', 'verb'),
    ('saber', 'to know (a fact)', 'verb'), ('conocer', 'to know (a person, a place)', 'verb'),
    ('ver', 'to see', 'verb'), ('dar', 'to give', 'verb'), ('decir', 'to say', 'verb'),
    ('salir', 'to go out', 'verb'), ('llegar', 'to arrive', 'verb travel'),
    ('buscar', 'to look for', 'verb'), ('encontrar', 'to find', 'verb'),
    ('pensar', 'to think', 'verb'), ('dormir', 'to sleep', 'verb'),
    ('leer', 'to read', 'verb'), ('escribir', 'to write', 'verb'),
    ('abrir', 'to open', 'verb'), ('cerrar', 'to close', 'verb'),
    ('comprar', 'to buy', 'verb'), ('pagar', 'to pay', 'verb travel'),
    ('llevar', 'to carry; to wear', 'verb'), ('esperar', 'to wait; to hope', 'verb'),
    ('empezar', 'to begin', 'verb'), ('terminar', 'to finish', 'verb'),
    ('ayudar', 'to help', 'verb'), ('aprender', 'to learn', 'verb'),
    ('olvidar', 'to forget', 'verb'), ('recordar', 'to remember', 'verb'),
    ('grande', 'big', 'adjective'), ('pequeño', 'small', 'adjective'),
    ('bueno', 'good', 'adjective'), ('malo', 'bad', 'adjective'),
    ('nuevo', 'new', 'adjective'), ('viejo', 'old', 'adjective'),
    ('caliente', 'hot', 'adjective'), ('frío', 'cold', 'adjective'),
    ('rápido', 'fast', 'adjective'), ('lento', 'slow', 'adjective'),
    ('fácil', 'easy', 'adjective'), ('difícil', 'difficult', 'adjective'),
    ('bonito', 'pretty', 'adjective'), ('feo', 'ugly', 'adjective'),
    ('caro', 'expensive', 'adjective travel'), ('barato', 'cheap', 'adjective travel'),
    ('lleno', 'full', 'adjective'), ('vacío', 'empty', 'adjective'),
    ('cansado', 'tired', 'adjective'), ('feliz', 'happy', 'adjective'),
    ('triste', 'sad', 'adjective'), ('limpio', 'clean', 'adjective'),
    ('sucio', 'dirty', 'adjective'), ('cerca', 'near', 'adverb'),
    ('lejos', 'far', 'adverb'), ('temprano', 'early', 'adverb time'),
    ('tarde', 'late', 'adverb time'), ('siempre', 'always', 'adverb time'),
    ('nunca', 'never', 'adverb time'), ('también', 'also', 'adverb'),
]

# (sentence with the verb clozed, the rule, tags)
GRAMMAR = [
    ('Yo {{c1::hablo}} español con mi abuela. (hablar)',
     'Present, yo: -ar verbs take -o.', 'present'),
    ('Tú {{c1::comes}} demasiado rápido. (comer)',
     'Present, tú: -er verbs take -es.', 'present'),
    ('Ella {{c1::vive}} en Sevilla desde 2019. (vivir)',
     'Present, él/ella: -ir verbs take -e.', 'present'),
    ('Nosotros {{c1::trabajamos}} los sábados. (trabajar)',
     'Present, nosotros: -ar verbs take -amos.', 'present'),
    ('Ellos {{c1::aprenden}} alemán este año. (aprender)',
     'Present, ellos: -er verbs take -en.', 'present'),
    ('Yo {{c1::tengo}} dos hermanas. (tener)',
     'Tener is irregular in the yo form: tengo.', 'present irregular'),
    ('Yo {{c1::hago}} la cena esta noche. (hacer)',
     'Hacer is irregular in the yo form: hago.', 'present irregular'),
    ('Tú {{c1::puedes}} venir mañana. (poder)',
     'Poder changes its stem: o becomes ue.', 'present stem-change'),
    ('Ella {{c1::quiere}} un café. (querer)',
     'Querer changes its stem: e becomes ie.', 'present stem-change'),
    ('Nosotros {{c1::queremos}} ir a la playa. (querer)',
     'Stem changes do not apply to nosotros and vosotros.', 'present stem-change'),
    ('Ayer yo {{c1::fui}} al mercado. (ir)',
     'Preterite of ir, yo: fui (the same form as ser).', 'preterite irregular'),
    ('Ayer ella {{c1::compró}} un abrigo nuevo. (comprar)',
     'Preterite, él/ella: -ar verbs take -ó.', 'preterite'),
    ('Anoche nosotros {{c1::comimos}} paella. (comer)',
     'Preterite, nosotros: -er verbs take -imos.', 'preterite'),
    ('El año pasado ellos {{c1::vivieron}} en Lima. (vivir)',
     'Preterite, ellos: -ir verbs take -ieron.', 'preterite'),
    ('Tú {{c1::hiciste}} un buen trabajo. (hacer)',
     'Preterite of hacer, tú: hiciste.', 'preterite irregular'),
    ('Yo {{c1::tuve}} que salir temprano. (tener)',
     'Preterite of tener, yo: tuve.', 'preterite irregular'),
    ('Cuando era niño, yo {{c1::jugaba}} en la calle. (jugar)',
     'Imperfect, yo: -ar verbs take -aba. The imperfect is for habits in the past.',
     'imperfect'),
    ('Mi abuelo {{c1::leía}} el periódico cada mañana. (leer)',
     'Imperfect, él: -er verbs take -ía.', 'imperfect'),
    ('Nosotros {{c1::éramos}} muy felices allí. (ser)',
     'Imperfect of ser: era, eras, era, éramos, erais, eran.', 'imperfect irregular'),
    ('Mañana {{c1::iré}} al dentista. (ir)',
     'Future: the infinitive plus -é.', 'future'),
    ('Ellos {{c1::vendrán}} a las ocho. (venir)',
     'The future of venir has an irregular stem: vendr-.', 'future irregular'),
    ('Nosotros {{c1::tendremos}} tiempo el domingo. (tener)',
     'The future of tener: tendr- plus the endings.', 'future irregular'),
    ('Yo {{c1::viajaría}} más si tuviera dinero. (viajar)',
     'Conditional: the infinitive plus -ía.', 'conditional'),
    ('¿Tú {{c1::podrías}} ayudarme? (poder)',
     'Conditional of poder: podr- plus -ías.', 'conditional irregular'),
    ('Quiero que tú {{c1::vengas}} a la fiesta. (venir)',
     'Present subjunctive after querer que: vengas.', 'subjunctive'),
    ('Espero que ella {{c1::esté}} bien. (estar)',
     'Present subjunctive of estar: esté.', 'subjunctive'),
    ('Es importante que nosotros {{c1::hablemos}} con él. (hablar)',
     'Present subjunctive, nosotros: -ar verbs take -emos.', 'subjunctive'),
    ('No creo que ellos {{c1::sepan}} la respuesta. (saber)',
     'Subjunctive of saber: sepa, sepas, sepa, sepamos, sepáis, sepan.',
     'subjunctive irregular'),
    ('Ojalá {{c1::llueva}} mañana. (llover)',
     'Ojalá takes the subjunctive: llueva.', 'subjunctive'),
    ('Si yo {{c1::tuviera}} tiempo, leería más. (tener)',
     'Imperfect subjunctive after si, for a condition that is not so: tuviera.',
     'subjunctive'),
    ('{{c1::Habla}} más despacio, por favor. (hablar, tú)',
     'Affirmative tú command: the él form of the present.', 'imperative'),
    ('No {{c1::comas}} eso. (comer, tú)',
     'Negative tú command: the present subjunctive.', 'imperative'),
    ('{{c1::Pongan}} los libros aquí. (poner, ustedes)',
     'Ustedes command: the subjunctive form, pongan.', 'imperative irregular'),
    ('Yo {{c1::he visto}} esa película. (ver)',
     'Present perfect: haber plus the past participle; visto is irregular.', 'perfect'),
    ('Nosotros {{c1::hemos escrito}} tres cartas. (escribir)',
     'Present perfect; escrito is an irregular participle.', 'perfect'),
    ('Ella {{c1::está leyendo}} en el jardín. (leer)',
     'Present progressive: estar plus the gerund, leyendo.', 'progressive'),
    ('Ellos {{c1::se levantan}} a las seis. (levantarse)',
     'A reflexive verb: the pronoun se with the ellos form.', 'reflexive'),
    ('Yo {{c1::me ducho}} antes de desayunar. (ducharse)',
     'Reflexive, yo: me ducho.', 'reflexive'),
    ('A mí {{c1::me gustan}} las manzanas. (gustar)',
     'Gustar agrees with the thing liked: las manzanas, so gustan.', 'gustar'),
    ('Nos {{c1::encanta}} el mar. (encantar)',
     'Encantar works like gustar: el mar is singular, so encanta.', 'gustar'),
]

# (ISO code for the flag, country, capital, continent tag)
CAPITALS = [
    ('PT', 'Portugal', 'Lisbon', 'europe'), ('ES', 'Spain', 'Madrid', 'europe'),
    ('FR', 'France', 'Paris', 'europe'), ('DE', 'Germany', 'Berlin', 'europe'),
    ('IT', 'Italy', 'Rome', 'europe'), ('NL', 'Netherlands', 'Amsterdam', 'europe'),
    ('BE', 'Belgium', 'Brussels', 'europe'), ('CH', 'Switzerland', 'Bern', 'europe'),
    ('AT', 'Austria', 'Vienna', 'europe'), ('PL', 'Poland', 'Warsaw', 'europe'),
    ('CZ', 'Czechia', 'Prague', 'europe'), ('HU', 'Hungary', 'Budapest', 'europe'),
    ('GR', 'Greece', 'Athens', 'europe'), ('SE', 'Sweden', 'Stockholm', 'europe'),
    ('NO', 'Norway', 'Oslo', 'europe'), ('DK', 'Denmark', 'Copenhagen', 'europe'),
    ('FI', 'Finland', 'Helsinki', 'europe'), ('IE', 'Ireland', 'Dublin', 'europe'),
    ('IS', 'Iceland', 'Reykjavík', 'europe'), ('RO', 'Romania', 'Bucharest', 'europe'),
    ('BG', 'Bulgaria', 'Sofia', 'europe'), ('HR', 'Croatia', 'Zagreb', 'europe'),
    ('RS', 'Serbia', 'Belgrade', 'europe'), ('UA', 'Ukraine', 'Kyiv', 'europe'),
    ('EE', 'Estonia', 'Tallinn', 'europe'), ('LV', 'Latvia', 'Riga', 'europe'),
    ('LT', 'Lithuania', 'Vilnius', 'europe'), ('SK', 'Slovakia', 'Bratislava', 'europe'),
    ('SI', 'Slovenia', 'Ljubljana', 'europe'),
    ('JP', 'Japan', 'Tokyo', 'asia'), ('CN', 'China', 'Beijing', 'asia'),
    ('KR', 'South Korea', 'Seoul', 'asia'), ('IN', 'India', 'New Delhi', 'asia'),
    ('TH', 'Thailand', 'Bangkok', 'asia'), ('VN', 'Vietnam', 'Hanoi', 'asia'),
    ('ID', 'Indonesia', 'Jakarta', 'asia'), ('MY', 'Malaysia', 'Kuala Lumpur', 'asia'),
    ('PH', 'Philippines', 'Manila', 'asia'), ('PK', 'Pakistan', 'Islamabad', 'asia'),
    ('BD', 'Bangladesh', 'Dhaka', 'asia'), ('NP', 'Nepal', 'Kathmandu', 'asia'),
    ('MN', 'Mongolia', 'Ulaanbaatar', 'asia'), ('KZ', 'Kazakhstan', 'Astana', 'asia'),
    ('UZ', 'Uzbekistan', 'Tashkent', 'asia'), ('IR', 'Iran', 'Tehran', 'asia'),
    ('IQ', 'Iraq', 'Baghdad', 'asia'), ('SA', 'Saudi Arabia', 'Riyadh', 'asia'),
    ('TR', 'Türkiye', 'Ankara', 'asia'), ('JO', 'Jordan', 'Amman', 'asia'),
    ('LB', 'Lebanon', 'Beirut', 'asia'), ('GE', 'Georgia', 'Tbilisi', 'asia'),
    ('AM', 'Armenia', 'Yerevan', 'asia'), ('KH', 'Cambodia', 'Phnom Penh', 'asia'),
    ('EG', 'Egypt', 'Cairo', 'africa'), ('MA', 'Morocco', 'Rabat', 'africa'),
    ('DZ', 'Algeria', 'Algiers', 'africa'), ('TN', 'Tunisia', 'Tunis', 'africa'),
    ('NG', 'Nigeria', 'Abuja', 'africa'), ('GH', 'Ghana', 'Accra', 'africa'),
    ('SN', 'Senegal', 'Dakar', 'africa'), ('ET', 'Ethiopia', 'Addis Ababa', 'africa'),
    ('KE', 'Kenya', 'Nairobi', 'africa'), ('TZ', 'Tanzania', 'Dodoma', 'africa'),
    ('UG', 'Uganda', 'Kampala', 'africa'), ('MZ', 'Mozambique', 'Maputo', 'africa'),
    ('ZM', 'Zambia', 'Lusaka', 'africa'), ('ZW', 'Zimbabwe', 'Harare', 'africa'),
    ('NA', 'Namibia', 'Windhoek', 'africa'), ('BW', 'Botswana', 'Gaborone', 'africa'),
    ('MG', 'Madagascar', 'Antananarivo', 'africa'), ('ML', 'Mali', 'Bamako', 'africa'),
    ('CM', 'Cameroon', 'Yaoundé', 'africa'), ('AO', 'Angola', 'Luanda', 'africa'),
    ('RW', 'Rwanda', 'Kigali', 'africa'),
    ('CA', 'Canada', 'Ottawa', 'americas'), ('US', 'United States', 'Washington, D.C.',
                                             'americas'),
    ('MX', 'Mexico', 'Mexico City', 'americas'), ('GT', 'Guatemala', 'Guatemala City',
                                                  'americas'),
    ('CU', 'Cuba', 'Havana', 'americas'), ('JM', 'Jamaica', 'Kingston', 'americas'),
    ('CO', 'Colombia', 'Bogotá', 'americas'), ('VE', 'Venezuela', 'Caracas', 'americas'),
    ('PE', 'Peru', 'Lima', 'americas'), ('EC', 'Ecuador', 'Quito', 'americas'),
    ('BR', 'Brazil', 'Brasília', 'americas'), ('AR', 'Argentina', 'Buenos Aires', 'americas'),
    ('CL', 'Chile', 'Santiago', 'americas'), ('UY', 'Uruguay', 'Montevideo', 'americas'),
    ('PY', 'Paraguay', 'Asunción', 'americas'), ('CR', 'Costa Rica', 'San José', 'americas'),
    ('PA', 'Panama', 'Panama City', 'americas'),
    ('AU', 'Australia', 'Canberra', 'oceania'), ('NZ', 'New Zealand', 'Wellington', 'oceania'),
    ('FJ', 'Fiji', 'Suva', 'oceania'), ('PG', 'Papua New Guinea', 'Port Moresby', 'oceania'),
    ('WS', 'Samoa', 'Apia', 'oceania'),
]

# (question, answer, tags)
CELL = [
    ("Which organelle produces most of the cell's ATP?",
     'The mitochondrion, by cellular respiration.', 'organelle energy'),
    ('What is the function of the nucleus?',
     "It holds the cell's DNA and controls transcription.", 'organelle nucleus'),
    ('Where are ribosomes assembled?', 'In the nucleolus, inside the nucleus.',
     'organelle nucleus'),
    ('What does the rough endoplasmic reticulum do?',
     'It folds and modifies the proteins made by the ribosomes on its surface.',
     'organelle protein'),
    ('What does the smooth endoplasmic reticulum do?',
     'It makes lipids and detoxifies drugs.', 'organelle'),
    ('What is the role of the Golgi apparatus?',
     'It sorts, modifies and packages proteins for secretion or delivery.',
     'organelle protein'),
    ('What do lysosomes contain?',
     'Digestive enzymes that break down worn-out organelles and engulfed material.',
     'organelle'),
    ('What is the cytoskeleton made of?',
     'Microfilaments (actin), intermediate filaments and microtubules.', 'structure'),
    ('What is the plasma membrane made of?',
     'A phospholipid bilayer with proteins embedded in it.', 'membrane'),
    ('Which organelle carries out photosynthesis?', 'The chloroplast.', 'organelle plant'),
    ('What is the cell wall of a plant made of?', 'Cellulose.', 'plant structure'),
    ('What does the central vacuole do in a plant cell?',
     'It stores water and keeps the cell turgid.', 'organelle plant'),
    ('What are peroxisomes for?',
     'Breaking down fatty acids and hydrogen peroxide.', 'organelle'),
    ('Which structure organizes the microtubules of an animal cell?',
     'The centrosome, with its pair of centrioles.', 'structure'),
    ('What are the folds of the inner mitochondrial membrane called?', 'Cristae.',
     'organelle energy'),
    ('What is the fluid inside the mitochondrion called?', 'The matrix.', 'organelle energy'),
    ('What is the fluid filling the chloroplast called?', 'The stroma.', 'organelle plant'),
    ('What are the flattened sacs inside a chloroplast called?',
     'Thylakoids, stacked into grana.', 'organelle plant'),
    ('What is the cytosol?',
     'The fluid part of the cytoplasm, outside the organelles.', 'structure'),
    ('What separates prokaryotic cells from eukaryotic ones?',
     'Prokaryotes have no nucleus and no membrane-bound organelles.', 'structure'),
    ('Where is the DNA of a prokaryote kept?',
     'In the nucleoid region, which no membrane encloses.', 'structure'),
    ('What are the pores of the nuclear envelope for?',
     'Letting RNA and proteins pass between the nucleus and the cytoplasm.',
     'nucleus membrane'),
    ('What is chromatin?', 'DNA wound around histone proteins.', 'nucleus'),
    ('What is a vesicle?',
     'A small membrane-bound sac that carries material within the cell.', 'transport'),
    ('What is exocytosis?',
     'A vesicle fusing with the plasma membrane to release its contents outside.',
     'transport membrane'),
    ('What is endocytosis?',
     'The membrane folding inwards to take material into the cell.', 'transport membrane'),
    ('What is osmosis?',
     'The diffusion of water across a selectively permeable membrane.', 'transport'),
    ('What does active transport need that diffusion does not?',
     'Energy, usually from ATP.', 'transport'),
    ('What are cilia and flagella built from?',
     'Microtubules in a 9 + 2 arrangement.', 'structure'),
    ('What do mitochondria and chloroplasts have in common with bacteria?',
     'Their own DNA and ribosomes: the endosymbiotic origin.', 'organelle'),
    ('What is the nuclear envelope?',
     'The double membrane that separates the nucleus from the cytoplasm.',
     'nucleus membrane'),
    ('What do free ribosomes make?', 'Proteins that stay in the cytosol.', 'protein'),
    ('What do bound ribosomes make?',
     'Proteins for membranes, organelles or secretion.', 'protein'),
    ('What is the extracellular matrix?',
     'A network of proteins and carbohydrates outside animal cells.', 'structure'),
    ('What connects neighbouring plant cells?',
     'Plasmodesmata: channels through the cell walls.', 'plant structure'),
    ('What are tight junctions?',
     'Seals between animal cells that stop leakage between them.', 'membrane'),
    ('What are gap junctions?',
     'Channels between animal cells that let ions and small molecules pass.', 'membrane'),
    ('What is the glycocalyx?',
     'The carbohydrate coat on the outside of the plasma membrane.', 'membrane'),
    ('What does the fluid mosaic model describe?',
     'The membrane as a fluid lipid bilayer with proteins moving in it.', 'membrane'),
    ('What is autophagy?',
     'The cell digesting its own damaged parts in lysosomes.', 'organelle'),
]

# (kana, romaji, tags)
KANA = [
    ('あ', 'a'), ('い', 'i'), ('う', 'u'), ('え', 'e'), ('お', 'o'),
    ('か', 'ka'), ('き', 'ki'), ('く', 'ku'), ('け', 'ke'), ('こ', 'ko'),
    ('さ', 'sa'), ('し', 'shi'), ('す', 'su'), ('せ', 'se'), ('そ', 'so'),
    ('た', 'ta'), ('ち', 'chi'), ('つ', 'tsu'), ('て', 'te'), ('と', 'to'),
    ('な', 'na'), ('に', 'ni'), ('ぬ', 'nu'), ('ね', 'ne'), ('の', 'no'),
    ('は', 'ha'), ('ひ', 'hi'), ('ふ', 'fu'), ('へ', 'he'), ('ほ', 'ho'),
    ('ま', 'ma'), ('み', 'mi'), ('む', 'mu'), ('め', 'me'), ('も', 'mo'),
    ('や', 'ya'), ('ゆ', 'yu'), ('よ', 'yo'),
    ('ら', 'ra'), ('り', 'ri'), ('る', 'ru'), ('れ', 're'), ('ろ', 'ro'),
    ('わ', 'wa'), ('を', 'wo'), ('ん', 'n'),
]
KANA_WORDS = [
    ('みず', 'mizu', 'water'), ('ねこ', 'neko', 'cat'), ('いぬ', 'inu', 'dog'),
    ('やま', 'yama', 'mountain'), ('かわ', 'kawa', 'river'), ('そら', 'sora', 'sky'),
    ('ほん', 'hon', 'book'), ('くるま', 'kuruma', 'car'), ('さかな', 'sakana', 'fish'),
    ('はな', 'hana', 'flower'),
]

# (text with clozes, back extra, tags)
PROGRAMMING = [
    ('Open a file that closes itself: <code>{{c1::with}} open(path) as f:</code>',
     'The file is closed when the block ends, even on an exception.', 'syntax'),
    ('<code>{{c1::enumerate}}(items, start=1)</code> yields (index, item) pairs.',
     '', 'builtin'),
    ('<code>sorted(words, key={{c1::len}})</code> orders strings by their length.',
     'Any function of one item works as a key.', 'builtin'),
    ('<code>{{c1::zip}}(names, ages)</code> pairs two iterables element by element.',
     'It stops at the shorter one; <code>strict=True</code> raises instead.', 'builtin'),
    ('Look a key up with a fallback: <code>d.{{c1::get}}(key, default)</code>', '', 'dict'),
    ('<code>collections.{{c1::Counter}}(words).most_common(3)</code> gives the three '
     'commonest words.', '', 'stdlib'),
    ('<code>collections.{{c1::defaultdict}}(list)</code> starts a missing key as an empty '
     'list.', '', 'stdlib dict'),
    ('Format to two decimals: <code>f"{value:{{c1::.2f}}}"</code>', '', 'strings'),
    ('<code>pathlib.Path(p).{{c1::read_text}}()</code> reads a whole file as a string.',
     'And <code>write_text()</code> writes one.', 'stdlib'),
    ('<code>{{c1::isinstance}}(x, (int, float))</code> checks against several types at '
     'once.', '', 'builtin'),
    ('A function that takes any keyword arguments: <code>def f(**{{c1::kwargs}}):</code>',
     'Positional ones: <code>*args</code>.', 'syntax'),
    ('Unpack the rest of a sequence: <code>first, *{{c1::rest}} = items</code>', '',
     'syntax'),
    ('Swap two variables in one line: <code>a, b = {{c1::b, a}}</code>', '', 'idiom'),
    ('A dataclass field with a mutable default: <code>{{c1::field}}(default_factory='
     'list)</code>', 'A bare <code>[]</code> default is shared by every instance.',
     'stdlib'),
    ('Make instances callable by defining <code>{{c1::__call__}}</code>.', '', 'classes'),
    ('Loop over keys and values together: <code>for k, v in d.{{c1::items}}():</code>',
     '', 'dict'),
    ('<code>itertools.{{c1::chain}}(a, b)</code> iterates over a, then b, without a new '
     'list.', '', 'stdlib'),
    ("<code>functools.{{c1::lru_cache}}</code> memoizes a function's results.",
     '<code>functools.cache</code> is the unbounded form.', 'stdlib'),
    ('Every element: <code>{{c1::all}}(x > 0 for x in xs)</code>; at least one: '
     '<code>{{c2::any}}(x > 0 for x in xs)</code>', '', 'builtin'),
    ('Join strings with a separator: <code>", ".{{c1::join}}(names)</code>', '',
     'strings'),
    ('Remove whitespace from both ends: <code>s.{{c1::strip}}()</code>',
     '<code>lstrip()</code> and <code>rstrip()</code> do one end.', 'strings'),
    ('Integer division rounds down: <code>7 {{c1:://}} 2 == 3</code>', '', 'syntax'),
    ('Raise to a power: <code>2 {{c1::**}} 10 == 1024</code>', '', 'syntax'),
    ('Re-raise the exception being handled, traceback intact: a bare '
     '<code>{{c1::raise}}</code>.', '', 'exceptions'),
    ('Run code whether or not an exception occurred: the <code>{{c1::finally}}</code> '
     'block.', '', 'exceptions'),
    ('A function becomes a generator when its body contains <code>{{c1::yield}}</code>.',
     '', 'syntax'),
    ('The main guard: <code>if __name__ == {{c1::"__main__"}}:</code>', '', 'idiom'),
    ('The immutable sequence type is <code>{{c1::tuple}}</code>; the mutable one '
     '<code>{{c2::list}}</code>.', '', 'builtin'),
    ('Set union: <code>a {{c1::|}} b</code>; intersection: <code>a {{c2::&amp;}} b</code>; '
     'difference: <code>a {{c3::-}} b</code>', '', 'syntax'),
    ('<code>str.{{c1::splitlines}}()</code> splits on any line ending.', '', 'strings'),
    ('<code>{{c1::json}}.dumps(obj, indent=2)</code> serializes to indented JSON.', '',
     'stdlib'),
    ('<code>{{c1::@property}}</code> turns a method into a read-only attribute.', '',
     'classes'),
    ('<code>{{c1::@staticmethod}}</code> defines a method that gets neither self nor cls.',
     '', 'classes'),
    ('<code>divmod(17, 5)</code> returns <code>{{c1::(3, 2)}}</code>.', '', 'builtin'),
    ('<code>round(2.5)</code> returns <code>{{c1::2}}</code>.',
     'Python rounds halves to the even neighbour.', 'builtin'),
]

DESCRIPTIONS = {
    'Spanish': 'Everyday words with their articles, and the grammar to string them '
               'together. Reviews first, then a handful of new words.',
    'Programming': 'Python idioms worth keeping at hand: the standard library, '
                   'comprehensions, context managers and the dunder methods.',
}

# Decks that are not Spanish are studied on alternate days; which days, per deck.
PARITY = {'Geography': 0, 'Biology': 1, 'Japanese': 0, 'Programming': 1}

# Words the learner keeps forgetting: tagged leech, and given a poor memory below.
LEECHES = ('conocer', 'llevar', 'esperar')


def flag(code):
    """A country's flag as text: the regional indicator letters of its ISO code."""
    return ''.join(chr(0x1F1E6 + ord(letter) - ord('A')) for letter in code)


# --- Diagrams ------------------------------------------------------------------------------

WIDTH, HEIGHT = 640, 440
INK = (0.2, 0.2, 0.25)


class Diagram:
    """A schematic drawn with cairo: shapes first, then labels, each of which becomes an
    occlusion rectangle (in fractions of the image) in the order it was drawn."""

    def __init__(self):
        self.surface = cairo.ImageSurface(cairo.FORMAT_RGB24, WIDTH, HEIGHT)
        self.ctx = cairo.Context(self.surface)
        self.ctx.set_source_rgb(0.985, 0.985, 0.975)
        self.ctx.paint()
        self.ctx.select_font_face('Sans', cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
        self.ctx.set_font_size(17)
        self.ctx.set_line_join(cairo.LINE_JOIN_ROUND)
        self.ctx.set_line_cap(cairo.LINE_CAP_ROUND)
        self.shapes = []

    def _paint(self, fill, stroke, width):
        if fill:
            self.ctx.set_source_rgb(*fill)
            self.ctx.fill_preserve()
        if stroke:
            self.ctx.set_source_rgb(*stroke)
            self.ctx.set_line_width(width)
            self.ctx.stroke()
        self.ctx.new_path()

    def ellipse(self, x, y, rx, ry, fill, stroke=INK, angle=0.0, width=2.5):
        self.ctx.save()
        self.ctx.translate(x, y)
        self.ctx.rotate(angle)
        self.ctx.scale(rx, ry)
        self.ctx.arc(0, 0, 1, 0, 2 * math.pi)
        self.ctx.restore()
        self._paint(fill, stroke, width)

    def rounded_rect(self, x, y, w, h, radius, fill, stroke=INK, width=2.5):
        ctx = self.ctx
        ctx.new_sub_path()
        ctx.arc(x + w - radius, y + radius, radius, -math.pi / 2, 0)
        ctx.arc(x + w - radius, y + h - radius, radius, 0, math.pi / 2)
        ctx.arc(x + radius, y + h - radius, radius, math.pi / 2, math.pi)
        ctx.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
        ctx.close_path()
        self._paint(fill, stroke, width)

    def line(self, points, stroke=INK, width=2.5):
        self.ctx.move_to(*points[0])
        for point in points[1:]:
            self.ctx.line_to(*point)
        self._paint(None, stroke, width)

    def curve(self, start, control1, control2, end, stroke=INK, width=2.5):
        self.ctx.move_to(*start)
        self.ctx.curve_to(*control1, *control2, *end)
        self._paint(None, stroke, width)

    def dot(self, x, y, radius, fill):
        self.ctx.arc(x, y, radius, 0, 2 * math.pi)
        self._paint(fill, None, 0)

    def label(self, text, x, y, to=None):
        """Text centred at (x, y) on a pale pill, a leader line to `to`; the pill's box
        is the occlusion."""
        ctx = self.ctx
        extents = ctx.text_extents(text)
        pad = 7
        left = x - extents.width / 2 - pad
        top = y - extents.height / 2 - pad
        width = extents.width + 2 * pad
        height = extents.height + 2 * pad
        if to:
            self.line([(x, y), to], stroke=(0.45, 0.45, 0.5), width=1.5)
            self.dot(to[0], to[1], 3, (0.45, 0.45, 0.5))
        self.rounded_rect(left, top, width, height, 6, (1, 1, 1), (0.75, 0.75, 0.8), 1)
        ctx.set_source_rgb(*INK)
        ctx.move_to(x - extents.width / 2 - extents.x_bearing,
                    y - extents.height / 2 - extents.y_bearing)
        ctx.show_text(text)
        ctx.new_path()
        self.shapes.append({
            'ordinal': len(self.shapes) + 1, 'shape': 'rect',
            'left': left / WIDTH, 'top': top / HEIGHT,
            'width': width / WIDTH, 'height': height / HEIGHT,
            'fill': None, 'angle': 0.0, 'occlude_inactive': False,
        })

    def png(self):
        buffer = io.BytesIO()
        self.surface.write_to_png(buffer)
        return buffer.getvalue()


def draw_cell(rng):
    d = Diagram()
    d.ellipse(320, 220, 268, 178, (0.93, 0.96, 0.9), (0.45, 0.6, 0.35), width=4)
    for x, y, angle in ((432, 150, 0.4), (420, 300, -0.5), (150, 300, 0.8)):
        d.ellipse(x, y, 44, 21, (0.98, 0.76, 0.5), (0.85, 0.5, 0.25), angle)
        for offset in (-18, -6, 6, 18):
            d.ctx.save()
            d.ctx.translate(x, y)
            d.ctx.rotate(angle)
            d.line([(offset, -12), (offset, 12)], (0.85, 0.5, 0.25), 1.5)
            d.ctx.restore()
    for radius in (26, 36, 46, 56):
        d.ctx.arc(520, 232, radius, math.pi * 0.75, math.pi * 1.3)
        d._paint(None, (0.85, 0.55, 0.75), 5)
    d.ellipse(250, 212, 70, 60, (0.8, 0.74, 0.92), (0.5, 0.4, 0.72))
    d.ellipse(240, 204, 22, 18, (0.6, 0.5, 0.82), (0.5, 0.4, 0.72))
    d.curve((330, 190), (360, 150), (380, 260), (340, 290), (0.5, 0.4, 0.72), 2)
    for _ in range(40):
        angle = rng.uniform(0, 2 * math.pi)
        radius = rng.uniform(0.3, 0.92)
        x = 320 + 268 * radius * math.cos(angle)
        y = 220 + 178 * radius * math.sin(angle)
        if math.hypot((x - 250) / 76, (y - 212) / 66) > 1:
            d.dot(x, y, 3, (0.35, 0.45, 0.6))
    d.label('Nucleus', 110, 90, (205, 170))
    d.label('Mitochondrion', 470, 60, (440, 130))
    d.label('Golgi apparatus', 500, 360, (520, 290))
    d.label('Ribosome', 120, 400, (175, 330))
    d.label('Cell membrane', 560, 410, (525, 330))
    return d


def draw_heart(rng):
    d = Diagram()
    d.line([(235, 30), (235, 125)], (0.55, 0.65, 0.9), 26)
    d.curve((415, 130), (415, 50), (360, 40), (300, 50), (0.9, 0.45, 0.45), 26)
    blue, red = (0.72, 0.8, 0.95), (0.95, 0.65, 0.65)
    d.rounded_rect(195, 115, 125, 110, 24, blue)
    d.rounded_rect(330, 115, 125, 110, 24, red)
    d.rounded_rect(195, 235, 125, 150, 30, blue)
    d.rounded_rect(330, 235, 125, 150, 30, red)
    d.line([(257, 225), (257, 235)], INK, 6)
    d.line([(392, 225), (392, 235)], INK, 6)
    d.label('Right atrium', 90, 160, (195, 170))
    d.label('Right ventricle', 90, 320, (195, 310))
    d.label('Left atrium', 560, 160, (455, 170))
    d.label('Left ventricle', 560, 320, (455, 310))
    d.label('Aorta', 560, 50, (415, 70))
    return d


def draw_neuron(rng):
    d = Diagram()
    body = (0.95, 0.85, 0.6)
    for angle in (-0.9, -0.5, 0.2, 0.8, 1.4, -1.5, 2.6, 3.4):
        x = 150 + 95 * math.cos(angle + math.pi)
        y = 220 + 85 * math.sin(angle + math.pi)
        d.line([(150, 220), (x, y)], (0.85, 0.7, 0.4), 4)
        d.line([(x, y), (x + rng.uniform(-25, 25), y - rng.uniform(10, 30))],
               (0.85, 0.7, 0.4), 2.5)
    d.line([(200, 220), (530, 220)], (0.85, 0.7, 0.4), 8)
    d.ellipse(150, 220, 56, 50, body)
    d.ellipse(150, 220, 16, 15, (0.6, 0.5, 0.82), (0.5, 0.4, 0.72))
    for x in (250, 320, 390, 460):
        d.ellipse(x, 220, 28, 14, (0.72, 0.8, 0.95))
    for end in ((590, 165), (600, 205), (600, 240), (590, 275)):
        d.line([(530, 220), end], (0.85, 0.7, 0.4), 3)
        d.dot(end[0], end[1], 7, body)
    d.label('Dendrites', 80, 90, (95, 150))
    d.label('Cell body', 150, 350, (150, 272))
    d.label('Axon', 300, 330, (355, 226))
    d.label('Myelin sheath', 390, 110, (390, 206))
    d.label('Axon terminals', 520, 370, (585, 280))
    return d


def draw_eye(rng):
    d = Diagram()
    d.ellipse(300, 220, 150, 150, (0.995, 0.995, 0.99), INK, width=4)
    d.ctx.arc(300, 220, 136, -1.15, 1.15)
    d._paint(None, (0.9, 0.5, 0.5), 7)
    d.ellipse(160, 220, 30, 72, (0.85, 0.92, 1.0), (0.4, 0.55, 0.8))
    d.line([(190, 150), (190, 200)], (0.35, 0.5, 0.3), 9)
    d.line([(190, 240), (190, 290)], (0.35, 0.5, 0.3), 9)
    d.ellipse(205, 220, 18, 52, (0.78, 0.88, 1.0), (0.4, 0.55, 0.8))
    d.line([(445, 245), (560, 300)], (0.95, 0.85, 0.6), 24)
    d.label('Cornea', 100, 100, (140, 170))
    d.label('Lens', 200, 370, (205, 272))
    d.label('Retina', 440, 90, (415, 135))
    d.label('Optic nerve', 520, 380, (540, 300))
    return d


def draw_bone(rng):
    d = Diagram()
    bone = (0.96, 0.93, 0.85)
    d.rounded_rect(165, 190, 310, 60, 10, bone)
    d.ellipse(160, 220, 62, 72, bone)
    d.ellipse(480, 220, 62, 72, bone)
    d.rounded_rect(200, 208, 240, 24, 10, (0.95, 0.75, 0.6), (0.8, 0.5, 0.4))
    for x in (130, 160, 190, 450, 480, 510):
        d.dot(x, 220 + rng.uniform(-30, 30), 5, (0.9, 0.85, 0.75))
    d.label('Epiphysis', 110, 90, (140, 160))
    d.label('Diaphysis', 320, 340, (320, 252))
    d.label('Marrow cavity', 320, 110, (320, 212))
    d.label('Compact bone', 520, 350, (440, 246))
    return d


def draw_tooth(rng):
    d = Diagram()
    d.ellipse(285, 300, 26, 85, (0.95, 0.9, 0.8), angle=0.15)
    d.ellipse(355, 300, 26, 85, (0.95, 0.9, 0.8), angle=-0.15)
    d.line([(200, 205), (440, 205)], (0.95, 0.7, 0.75), 10)
    d.ellipse(320, 150, 92, 72, (0.995, 0.995, 0.975))
    d.ellipse(320, 152, 66, 50, (0.97, 0.9, 0.75), (0.85, 0.7, 0.5))
    d.ellipse(320, 172, 24, 36, (0.95, 0.55, 0.55), (0.8, 0.4, 0.4))
    d.line([(305, 205), (288, 350)], (0.95, 0.55, 0.55), 5)
    d.line([(335, 205), (352, 350)], (0.95, 0.55, 0.55), 5)
    d.label('Enamel', 130, 90, (240, 115))
    d.label('Dentine', 520, 110, (385, 140))
    d.label('Pulp', 520, 230, (344, 180))
    d.label('Root', 170, 350, (268, 330))
    return d


# (file name, header, back extra, tags, drawing)
DIAGRAMS = [
    ('animal-cell.png', 'Animal cell', 'A schematic; the organelles are not to scale.',
     'cell diagram', draw_cell),
    ('heart.png', 'Chambers of the heart',
     'Seen from the front, so the right side of the heart is on the left.',
     'heart diagram', draw_heart),
    ('neuron.png', 'A neuron', 'The signal travels from the dendrites along the axon.',
     'nervous diagram', draw_neuron),
    ('eye.png', 'The eye in section', 'Light enters through the cornea and the lens.',
     'eye diagram', draw_eye),
    ('long-bone.png', 'A long bone', 'The ends are spongy bone; the shaft is compact.',
     'skeleton diagram', draw_bone),
    ('tooth.png', 'A tooth in section', 'Enamel is the hardest tissue in the body.',
     'tooth diagram', draw_tooth),
]


# --- The notes, as lists per deck -----------------------------------------------------------

def note_lists(small):
    """{deck name: (stock kind, [(fields, tags), …])}, half of each list with `small`
    (the diagrams always all)."""
    vocabulary = [([spanish, english], tags.split() + (['leech'] if spanish in LEECHES else []))
                  for spanish, english, tags in VOCABULARY]
    grammar = [([text, rule], ['grammar'] + tags.split()) for text, rule, tags in GRAMMAR]
    capitals = [([f'{flag(code)} Capital of {country}', capital], [continent])
                for code, country, capital, continent in CAPITALS]
    cell = [([question, answer], tags.split()) for question, answer, tags in CELL]
    kana = [([kana, romaji], ['hiragana']) for kana, romaji in KANA]
    # Katakana mirror the hiragana table, 0x60 code points up.
    kana += [([chr(ord(kana) + 0x60), romaji], ['katakana']) for kana, romaji in KANA]
    kana += [([f'{word} <span style="color: gray">({english})</span>', romaji],
              ['hiragana', 'word']) for word, romaji, english in KANA_WORDS]
    programming = [([text, extra], ['python'] + tags.split())
                   for text, extra, tags in PROGRAMMING]
    lists = {
        'Spanish::Vocabulary': ('basic-reversed', vocabulary),
        'Spanish::Grammar': ('cloze', grammar),
        'Geography': ('basic', capitals),
        'Biology::Cell': ('basic', cell),
        'Japanese': ('basic-typed', kana),
        'Programming': ('cloze', programming),
    }
    if small:
        lists = {name: (kind, notes[::2]) for name, (kind, notes) in lists.items()}
    return lists


def anatomy_notes(collection, rng):
    """The image occlusion notes: each diagram drawn, stored in the media folder, and its
    labels turned into occlusion rectangles."""
    notes = []
    for name, header, extra, tags, draw in DIAGRAMS:
        diagram = draw(rng)
        stored = collection.media.add_bytes(diagram.png(), name)
        fields = [notetypes.occlusion_field(diagram.shapes), f'<img src="{stored}">', header,
                  extra, '']
        notes.append((fields, tags.split()))
    return notes


# --- The clock -------------------------------------------------------------------------------

class Clock:
    """The invented now, in Unix seconds; stands in for the `time` module inside the
    collection so ids, creation times and the review log follow it."""

    def __init__(self, now):
        self.now = now

    def time(self):
        return self.now

    def strftime(self, *args):
        return time.strftime(*args)

    def advance(self, seconds):
        self.now += seconds
        return self.now


# --- The learner ----------------------------------------------------------------------------

class Learner:
    """Answers cards against a hidden memory: FSRS's retrievability shifted by a per-card
    aptitude (leeches have a poor one), new cards by a prior."""

    def __init__(self, rng, scheduler, leech_note_ids):
        self.rng = rng
        self.scheduler = scheduler
        self.leech_note_ids = set(leech_note_ids)
        self.aptitude = {}

    def _aptitude(self, card):
        if card.id not in self.aptitude:
            if card.note_id in self.leech_note_ids:
                self.aptitude[card.id] = -1.3
            else:
                self.aptitude[card.id] = min(max(self.rng.gauss(0.0, 0.5), -0.9), 1.2)
        return self.aptitude[card.id]

    def rate(self, card, now):
        rng = self.rng
        aptitude = self._aptitude(card)
        if card.state == 'new':
            draw = rng.random() - aptitude * 0.1
            if draw < 0.12:
                return EASY
            if draw < 0.7:
                return GOOD
            if draw < 0.84:
                return HARD
            return AGAIN
        if card.in_learning:
            draw = rng.random() - aptitude * 0.05
            if draw < 0.05:
                return EASY
            if draw < 0.86:
                return GOOD
            if draw < 0.93:
                return HARD
            return AGAIN
        # The learner remembers somewhat better than FSRS's default parameters predict.
        recall = self.scheduler.retrievability(card, now) or 0.9
        recall = min(max(recall, 0.01), 0.99)
        chance = 1 / (1 + math.exp(-(math.log(recall / (1 - recall)) + 0.4 + aptitude)))
        if rng.random() > chance:
            return AGAIN
        if chance > 0.93 and rng.random() < 0.3:
            return EASY
        if chance < 0.65 or rng.random() < 0.08:
            return HARD
        return GOOD

    def duration(self, card, rating):
        seconds = self.rng.uniform(3, 11)
        if card.state == 'new':
            seconds += self.rng.uniform(1, 5)
        if rating in (AGAIN, HARD):
            seconds += self.rng.uniform(1, 5)
        return int(min(seconds, 20) * 1000)


def sit(collection, scheduler, learner, clock, deck_id, budget):
    """One sitting: the deck's queue, up to `budget` answers; how many were given."""
    session = scheduler.session(deck_id)
    answered = 0
    while answered < budget:
        card = session.next_card(clock.now)
        if card is None:
            break
        rating = learner.rate(card, clock.now)
        duration = learner.duration(card, rating)
        clock.advance(duration / 1000)
        after = scheduler.answer(card, rating, now=clock.now, duration_ms=duration)
        session.answered(after, rating)
        clock.advance(learner.rng.uniform(0.5, 2.5))
        answered += 1
    return answered


# --- Building ---------------------------------------------------------------------------------

def prepare_directory(directory):
    """Delete and recreate `directory`; refuse one that holds something other than a
    collection."""
    if directory.exists():
        if not directory.is_dir():
            sys.exit(f'{directory} is not a directory')
        entries = list(directory.iterdir())
        if entries and not (directory / 'collection.sqlite').exists():
            sys.exit(f'{directory} is not empty and holds no collection.sqlite: not deleting it')
        shutil.rmtree(directory)
    directory.mkdir(parents=True)


def schedule_batches(rng, lists, history_days):
    """When notes are added: {day index: [(deck name, fields, tags), …]}. About a third of
    each deck on the first day, the rest in batches spread over the months, the last one in
    the final days (for Spanish, on the final evening), so new cards wait today."""
    batches = {}
    for name, (_kind, notes) in lists.items():
        initial = max(1, int(len(notes) * 0.35))
        batches.setdefault(0, []).extend((name, fields, tags) for fields, tags in notes[:initial])
        rest = notes[initial:]
        size = rng.randint(8, 14) if name.startswith('Spanish') else rng.randint(5, 10)
        # Chunked from the end, so the last batch is a full one and the first the remainder.
        starts = list(range(len(rest), 0, -size))[::-1]
        chunks = [rest[max(start - size, 0):start] for start in starts]
        for index, chunk in enumerate(chunks):
            day = int((index + 1) / (len(chunks) + 1) * (history_days - 1))
            day = min(max(day + rng.randint(-2, 2), 1), history_days - 1)
            if index == len(chunks) - 1:
                day = history_days - (1 if name.startswith('Spanish') else rng.randint(1, 4))
            batches.setdefault(day, []).extend((name, fields, tags) for fields, tags in chunk)
    return batches


def add_batch(collection, decks, kinds, batch):
    for name, fields, tags in batch:
        collection.add_note(kinds[name].id, decks[name].id, fields, tags)


def unbury_before(collection, day):
    """What Collection.unbury_past() does at start, by the invented day: the collection
    reads the real clock for that."""
    collection.db.execute('UPDATE cards SET buried = 0 WHERE buried != 0 AND buried < ?',
                          (day,))


def ensure_due(collection, deck_ids, minimum, today):
    """Pull the review cards due soonest forward to today until the decks have `minimum`
    due; the number moved."""
    marks = ','.join('?' * len(deck_ids))
    due = collection.db.execute(
        f'SELECT COUNT(*) FROM cards WHERE state = ? AND due <= ? AND suspended = 0 '
        f'AND deck_id IN ({marks})', ['review', today, *deck_ids]).fetchone()[0]
    if due >= minimum:
        return 0
    rows = collection.db.execute(
        f'SELECT id FROM cards WHERE state = ? AND due > ? AND suspended = 0 '
        f'AND deck_id IN ({marks}) ORDER BY due, id LIMIT ?',
        ['review', today, *deck_ids, minimum - due]).fetchall()
    for row in rows:
        collection.db.execute('UPDATE cards SET due = ? WHERE id = ?', (today, row['id']))
    return len(rows)


def build(directory, seed, small):
    rng = random.Random(seed)
    random.seed(seed)  # the collection's guids
    history_days = SMALL_HISTORY_DAYS if small else HISTORY_DAYS
    break_days = SMALL_BREAK_DAYS if small else BREAK_DAYS
    today = days.today()
    first_day = today - history_days
    clock = Clock(days.day_start(first_day) + 5 * 3600)  # 9:00 on the first day
    collection_module.time = clock
    deck_config.time = clock

    prepare_directory(directory)
    collection = Collection(directory / 'collection.sqlite', backups=False)
    collection.db.execute('PRAGMA synchronous = OFF')  # thousands of small transactions
    scheduler = Scheduler(collection)

    lists = note_lists(small)
    decks = {name: collection.add_deck(name) for name in lists}
    decks['Biology::Anatomy'] = collection.add_deck('Biology::Anatomy')
    kinds = {name: collection.notetype_by_name(notetypes.stock(kind).name)
             for name, (kind, _notes) in lists.items()}
    kinds['Biology::Anatomy'] = collection.notetype_by_name(notetypes.stock('occlusion').name)
    for name, description in DESCRIPTIONS.items():
        deck = collection.deck_by_name(name)
        deck.description = description
        collection.update_deck(deck)
    heavy = collection.add_deck_config(deck_config.DeckConfig(
        name='Heavy', new_per_day=40, desired_retention=0.85))
    for name in ('Spanish', 'Spanish::Vocabulary', 'Spanish::Grammar'):
        collection.set_deck_config(collection.deck_by_name(name).id, heavy.id)
    lists['Biology::Anatomy'] = ('occlusion', anatomy_notes(collection, rng))
    batches = schedule_batches(rng, lists, history_days)

    leech_note_ids = [row['id'] for row in collection.db.execute(
        'SELECT id FROM notes WHERE tags LIKE ?', ('% leech %',))]
    learner = Learner(rng, scheduler, leech_note_ids)
    study_decks = {name: collection.deck_by_name(name).id for name in PARITY}
    spanish = collection.deck_by_name('Spanish')
    break_start = int(history_days * rng.uniform(0.4, 0.65))
    # The final day is skipped too, so a day's reviews wait with today's.
    skipped = {index for index in range(1, history_days)
               if break_start <= index < break_start + break_days
               or rng.random() < SKIP_CHANCE or index == history_days - 1}

    for index in range(history_days):
        day = first_day + index
        hour = rng.uniform(8, 21.5)
        clock.now = days.day_start(day) + (hour - days.DEFAULT_DAY_START_HOUR) * 3600
        unbury_before(collection, day)
        if index == 0:
            add_batch(collection, decks, kinds, batches.get(0, []))
        if index not in skipped:
            sit(collection, scheduler, learner, clock, spanish.id, rng.randint(40, 90))
            for name, parity in PARITY.items():
                if (index + parity) % 2 == 0:
                    clock.advance(rng.uniform(60, 300))
                    sit(collection, scheduler, learner, clock, study_decks[name],
                        rng.randint(20, 50))
        if index and index in batches:
            clock.advance(rng.uniform(600, 2400))
            add_batch(collection, decks, kinds, batches[index])
        leech_note_ids = [row['id'] for row in collection.db.execute(
            'SELECT id FROM notes WHERE tags LIKE ?', ('% leech %',))]
        learner.leech_note_ids = set(leech_note_ids)

    # The learner's own marks, made on the last evening.
    clock.advance(600)
    reviewed = collection.find_cards('deck:Geography -is:new', order='cards.id')
    collection.suspend(reviewed[:2])
    for number, query in enumerate(('deck:Spanish::Vocabulary -is:new', 'deck:Biology::Cell',
                                    'deck:Japanese', 'deck:Programming'), start=1):
        found = collection.find_cards(query, order='cards.id')
        if found:
            collection.set_flag([found[number * 3 % len(found)]], number)
    grammar = collection.find_notes('deck:Spanish::Grammar')
    collection.set_marked(grammar[:1], True)

    unbury_before(collection, today)
    moved = ensure_due(collection, collection.deck_and_children(spanish.id),
                       SPANISH_DUE_MINIMUM, today)
    collection.set('demo', True)
    collection.clear_undo()
    counts = scheduler.counts(spanish.id)
    summary = {
        'decks': len(collection.decks()), 'notes': collection.note_count(),
        'cards': collection.card_count(),
        'reviews': collection.db.execute('SELECT COUNT(*) FROM revlog').fetchone()[0],
        'spanish_today': counts, 'moved': moved,
    }
    collection.close()
    return summary


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--data-dir', default=str(ROOT / 'build' / 'demo'),
                        help='where to build it (default: build/demo)')
    parser.add_argument('--seed', type=int, default=1, help='the random seed (default: 1)')
    parser.add_argument('--small', action='store_true',
                        help='half the notes and 30 days of history, for the tests')
    args = parser.parse_args(argv)
    started = time.monotonic()
    summary = build(pathlib.Path(args.data_dir).resolve(), args.seed, args.small)
    new, learning, due = summary['spanish_today']
    print(f'demo collection: {summary["decks"]} decks, {summary["notes"]} notes, '
          f'{summary["cards"]} cards, {summary["reviews"]} reviews; Spanish today: {new} new, '
          f'{learning} learning, {due} due ({summary["moved"]} pulled forward); '
          f'{time.monotonic() - started:.1f} s')


if __name__ == '__main__':
    main()
