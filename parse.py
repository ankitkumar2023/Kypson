"""
RFQ parser: turns a short, messy packaging enquiry (English / Hinglish) into six fields.

    parse_rfq("bopp bags chahiye 40 hazaar, 65 gsm, Nagpur")
    -> {"category": "bopp_bag", "quantity": 40000, "unit": "pcs",
        "gsm": 65, "micron": None, "location": "Nagpur"}

Rules only, no model. The steps, in the order parse_rfq() runs them:
  1. clean_text      lowercase, "65gsm" -> "65 gsm", remove Indian commas, fix typos
  2. find_numbers    every number, incl. "40 hazaar", "2.5 lac", "dedh lakh", "15K"
  3. label_number    what each number is, from the words around it
                     (gsm, micron, size, price, phone, pincode, weight, quantity ...)
  4. find_category   keyword rules in a fixed priority order (the brief's traps)
  5. pick_quantity   the order quantity and its unit
  6. find_location   offline place list: exact match, then fuzzy match, then cue words
"""

import difflib
import re
import unicodedata
from functools import lru_cache


def words(text):
    return set(text.split())


def table(groups):
    """{"k thousand": 1000} -> {"k": 1000, "thousand": 1000}"""
    return {w: value for ws, value in groups.items() for w in ws.split()}


# ======================================================================================
# 1. Vocabulary  (to support a new word, add it to the right list here)
# ======================================================================================

# words that multiply the number before them: "40 hazaar", "2.5 lac", "15 k"
MULTIPLIERS = table({
    "k thousand thousands thou thsnd hazaar hazar hajar hazzar hjar hzr hazaro hazaaron "
    "hazara": 1_000,
    "lakh lakhs lac lacs lak lacks lack lkh lk l lacc laakh": 100_000,
    "crore crores cr karod karor crs": 10_000_000,
    "million millions mn mil": 1_000_000,
    "hundred sau": 100,
    "dozen": 12,
})

# number words; trusted only before a multiplier ("do lakh") because "do", "das" and
# "one" are also normal words. English ones are also trusted before a unit ("ten bags").
WORD_NUMBERS = table({
    "one a an ek": 1, "two do": 2, "three teen tin": 3, "four char chaar": 4,
    "five paanch panch pach": 5, "six chhe che chah": 6, "seven saat sat": 7,
    "eight aath ath": 8, "nine nau": 9, "ten das dus": 10, "eleven gyarah": 11,
    "twelve barah baarah": 12, "fifteen pandrah pandra": 15, "twenty bees bis": 20,
    "twentyfive pachis pachees pachchis": 25, "thirty tees tis": 30,
    "forty fourty chalis chaalis chalees": 40, "fifty pachas pachaas": 50,
    "sixty saath": 60, "seventy sattar": 70, "eighty assi": 80, "ninety nabbe": 90,
})
ENGLISH_NUMBERS = words("one two three four five six seven eight nine ten eleven twelve "
                        "fifteen twenty thirty forty fourty fifty sixty seventy eighty ninety")

# "dedh lakh" = 1.5 lakh, "dhai hazaar" = 2.5 thousand
FRACTIONS = table({"dedh": 1.5, "dhai dhaai adhai dhaee": 2.5, "aadha adha half": 0.5})
# "sadhe teen lakh" = 3.5 lakh, "sava lakh" = 1.25 lakh, "paune do lakh" = 1.75 lakh
ADD_TO_NEXT = table({"sadhe saadhe sade saade": 0.5, "sava sawa savva": 0.25,
                     "paune pone paone": -0.25})
SAVA = words("sava sawa savva")

# units written after a number
GSM_WORDS = words("gsm gm gms g grams gram gramm grm gsmm gsms")
MICRON_WORDS = words("micron microns mic mu um mcr micr mics mc mircon miron micorn "
                     "microne mikron")
GAUGE_WORDS = words("gauge gage guage gaze")
KG_WORDS = words("kg kgs kilo kilos kilogram kilograms kgms killo")
TON_WORDS = words("ton tons tonne tonnes mt tonn tn mts")
QUINTAL_WORDS = words("quintal quintals qtl")
PCS_WORDS = words("pcs pc piece pieces peices nos no numbers number nag nug units unit qty "
                  "pics pis pec psc bags bag sacks sack bori boriya katta kattas boxes box "
                  "cartons carton cartoons pkt pkts packets sheets set sets pouch pouches "
                  "sachet sachets")
ROLL_WORDS = words("roll rolls rol rools")
SIZE_WORDS = words("inch inches inchs mm cm cms mtr mtrs meter meters metre metres ft feet "
                   "foot m") | {'"', "'"}
SPEC_WORDS = words("ply plies layer layers colour colours color colors col clr side sides "
                   "fold loop loops panel bcf ect bf x % percent degree deg c ml ltr litre "
                   "liter litres liters lit")
TIME_WORDS = words("day days din dino week weeks hafte hafta month months mahine mahina "
                   "year years saal hours hrs hr am pm baje tarikh date")
PER_PERIOD = words("month monthly mahina mahine week weekly year yearly annum annual day "
                   "daily hafta hafte saal")
UNIT_WORDS = (GSM_WORDS | MICRON_WORDS | GAUGE_WORDS | KG_WORDS | TON_WORDS | QUINTAL_WORDS
              | PCS_WORDS | ROLL_WORDS | SIZE_WORDS | SPEC_WORDS)

# words around a number that tell us what it is
PRICE_BEFORE = words("rs inr @ rupees rupaye rupay price rate budget cost")
PRICE_AFTER = words("rs rupees rupaye rupay rupee paisa paise inr /- only")
PHONE_BEFORE = words("+ phone mob mobile ph call whatsapp contact")
PIN_BEFORE = words("pin pincode zip postal code pin-code")
QTY_BEFORE = words("qty quantity qnty qnt qtty quanity quantiy moq requirement req need "
                   "chahiye want order")
CAPACITY_WORDS = words("capacity cap capicity capcity load swl loading packing pack fill "
                       "filling wala wali wale bharne hold holding weight wt size each per")
FILL_AFTER = CAPACITY_WORDS | words("bag bags sack sacks bori box boxes jumbo fibc ka ki ke "
                                    "katta carton cartons")
RANGE_WORDS = words("- to se ~ or /")
# words that start a product name ("1000 bopp bags", "5000 pp woven sacks")
PRODUCT_WORDS = words("bopp woven jumbo fibc pp hdpe laminated corrugated mono duplex shrink "
                      "stretch cling ldpe cement adstar bulk big plain printed stand zipper")

# domain words used for typo correction ("wovan" -> "woven")
TYPO_VOCAB = ("jumbo fibc bulk woven sack sacks shrink stretch film corrugated carton cartons "
              "boxes laminated lamination bopp cement capacity micron microns quantity pieces "
              "thousand hazaar lakh crore delivery required plastic packing fertilizer "
              "polypropylene transparent printed printing unlaminated pouch pouches mono "
              "duplex sachet zipper").split()
# typos too short for fuzzy matching
SHORT_TYPOS = {"sak": "sack", "sac": "sack", "sacs": "sacks", "saks": "sacks", "bop": "bopp",
               "boop": "bopp", "bx": "box", "bxs": "boxes", "boxs": "boxes", "boxe": "boxes",
               "flm": "film", "flim": "film", "fiml": "film", "fbc": "fibc", "fibic": "fibc",
               "fbic": "fibc"}

# words that are never a place name
STOPWORDS = words("""
a an the and or of for to in at on by with from into per is are be am was were it its this
that these those i me my we our us you your he she they them their need needed needs want
wanted require required requirement requirements req reqd pls plz please kindly urgent
urgently asap immediately send share quote quotation rate rates price prices best lowest
offer supply supplier suppliers deliver delivery delivered dispatch shipping ship location
loc city site plant factory warehouse godown address area near nearby around approx
approximately about total monthly weekly daily month week year each every also only just
more than less minimum min max maximum upto up till sir madam ji bhai bhaiya dear hello hi
hey team regards thanks thank thx namaste chahiye chaiye chahie chaahiye chaheye chiye
chahye chaye chaiyeh chahiyen hai hain he ho hoga hogi hona chahte chahta chahti karna karo
kardo kar kare karein bhejo bhej bhejna bhejiye batao bataye bataiye dijiye dena dedo de do
lena lene ka ki ke ko se me mein mai mei mujhe hume humein hamein hamko hum aap apka apki ye
yeh wo woh jo aur ya bhi tak liye wala wali wale waala waali chalega jaldi abhi kitna kitne
kitni bata batana milega milegi order orders ordr new fresh good quality high low strong
heavy light plain printed print printing color colour colours colors multicolor multi
single double side white black red blue green yellow brown natural transparent clear milky
bopp bop boop woven wovan sack sacks bag bags bori bora boriya katta kattas jumbo fibc bulk
big ton tonne tons film films shrink stretch cling wrap wrapping pof ldpe lldpe hdpe pp pe
pet polyester poly plastic corrugated corugated carton cartons cartoon cartoons box boxes
dabba dabbe peti ply laminated lamination laminate unlaminated non without cement adstar ad
star block bottom valve fertilizer fertiliser rice atta flour sugar grain feed seed chemical
chemicals pellet pellets sand food grade industrial export gsm gm gms grams micron microns
mic mu gauge kg kgs kilo tonnes mt pcs pc piece pieces nos no number numbers unit units qty
quantity roll rolls nag inch inches mm cm mtr meter size sizes width length height dimension
dimensions x thickness capacity load swl loop loops panel baffle liner lined inner outer top
spout discharge filling hazaar hazar lakh lac lacs crore cr thousand hundred dozen k rs inr
rupees paisa gst extra incl including included excluding pallet pallets packaging packing
pack packed pouch pouches tape tapes label labels sheet sheets bundle bundles pouchs sachet
sachets mono duplex folding zipper ziplock zip lock stand standup standy doypack spouted seal kraft
board paper medicine pharma cosmetic cosmetics sweet sweets mithai snacks namkeen import
imported port virgin recycled reprocessed uv treated stabilized stabilised hindi english one
two three four five ten twenty fifty tomorrow today kal aaj parso next weeks months days day
""")

# location cue words
CUE_BEFORE = words("in at to near delivery deliver delivered dispatch location loc city site "
                   "plant factory from ship supply destination godown warehouse place address "
                   "area frm @")
CUE_AFTER = words("me mein mai mei tak delivery plant site factory unit godown warehouse midc "
                  "gidc sidcul riico based wale wala dispatch")   # "Sinnar mein", "Taloja MIDC"
DELIVERY_CUES = words("to delivery deliver delivered dispatch ship destination tak me mein mai "
                      "in at")
CHAHIYE = re.compile(r"^cha+h?i?y?e+h?n?$|^chi?ye$|^chahi?y?e$")   # chahiye and its spellings

# Indian industrial towns and states the city database misses (hand list wins ties)
EXTRA_PLACES = """
Manesar, Rudrapur, Sri City, Baddi, Daman, Sanand, Pithampur, Greater Noida, Bawal,
Neemrana, Bhiwadi, Sitarganj, Kashipur, Haridwar, Dahej, Mundra, Kandla, Gandhidham,
Halol, Kalol, Mehsana, Chakan, Talegaon, Ranjangaon, Taloja, Tarapur, Boisar,
Ambernath, Butibori, Hingna, Mandideep, Sehore, Sangareddy, Patancheru, Jeedimetla,
Shamshabad, Medchal, Oragadam, Sriperumbudur, Irungattukottai, Hosur, Peenya,
Bommasandra, Doddaballapur, Nelamangala, Tumkur, Tumakuru, Jigani, Attibele,
Kundli, Sonipat, Panipat, Bahadurgarh, Rai, Barhi, Kathua, Samba, Nalagarh, Parwanoo,
Paonta Sahib, Selaqui, Sidcul, Lalru, Derabassi, Mohali, Zirakpur, Rajpura, Mandi Gobindgarh,
Kala Amb, Unnao, Sikandrabad, Dadri, Ghaziabad, Sahibabad, Loni, Bhagalpur, Hajipur,
Durgapur, Asansol, Howrah, Uluberia, Kalyani, Haldia, Siliguri, Guwahati, Jorhat,
Rourkela, Jharsuguda, Angul, Cuttack, Bhubaneswar, Khurda, Vizag, Visakhapatnam,
Vijayawada, Nellore, Tirupati, Kakinada, Rajahmundry, Coimbatore, Tiruppur, Erode,
Karur, Salem, Namakkal, Madurai, Sivakasi, Tuticorin, Thoothukudi, Trichy,
Tiruchirappalli, Pondicherry, Puducherry, Kochi, Cochin, Kakkanad, Aluva, Palakkad,
Thrissur, Kozhikode, Calicut, Mangalore, Mangaluru, Udupi, Belgaum, Belagavi, Hubli,
Dharwad, Davangere, Bellary, Ballari, Mysore, Mysuru, Raipur, Bhilai, Durg, Bilaspur,
Korba, Ranchi, Jamshedpur, Bokaro, Dhanbad, Patna, Gaya, Muzaffarpur, Varanasi,
Prayagraj, Allahabad, Lucknow, Kanpur, Agra, Mathura, Aligarh, Meerut, Moradabad,
Bareilly, Gorakhpur, Jhansi, Gwalior, Indore, Bhopal, Jabalpur, Ujjain, Dewas,
Ratlam, Kota, Bhilwara, Udaipur, Ajmer, Jaipur, Jodhpur, Bikaner, Alwar, Sikar,
Morbi, Morvi, Rajkot, Jamnagar, Bhavnagar, Junagadh, Anand, Nadiad, Bharuch,
Ankleshwar, Vapi, Valsad, Navsari, Silvassa, Umbergaon, Surat, Vadodara, Baroda,
Ahmedabad, Gandhinagar, Changodar, Vatva, Naroda, Odhav, Bhiwandi, Vasai, Virar,
Palghar, Thane, Navi Mumbai, Mumbai, Pune, Nashik, Aurangabad, Sambhajinagar,
Jalgaon, Dhule, Akola, Amravati, Nagpur, Wardha, Chandrapur, Solapur, Kolhapur,
Sangli, Satara, Ahmednagar, Latur, Nanded, Goa, Verna, Margao, Panaji, Ludhiana,
Jalandhar, Amritsar, Bathinda, Patiala, Chandigarh, Panchkula, Ambala, Karnal,
Yamunanagar, Hisar, Rohtak, Rewari, Faridabad, Gurgaon, Gurugram, Noida, Delhi,
New Delhi, Okhla, Narela, Bawana, Mayapuri, Naraina, Wazirpur, Jammu, Srinagar,
Dehradun, Rishikesh, Kolkata, Calcutta, Chennai, Madras, Ambattur, Guindy,
Hyderabad, Secunderabad, Bengaluru, Bangalore, Bombay, Poona,
Gujarat, Maharashtra, Rajasthan, Punjab, Haryana, Karnataka, Kerala, Telangana,
Andhra Pradesh, Tamil Nadu, Uttar Pradesh, Madhya Pradesh, West Bengal, Bihar,
Jharkhand, Odisha, Orissa, Assam, Uttarakhand, Himachal Pradesh, Chhattisgarh
"""
STATES = words("gujarat maharashtra rajasthan punjab haryana karnataka kerala telangana bihar "
               "jharkhand odisha orissa assam uttarakhand chhattisgarh goa") | {
    "andhra pradesh", "tamil nadu", "uttar pradesh", "madhya pradesh", "west bengal",
    "himachal pradesh"}

# export destinations as buyers write them (works even without geonamescache)
FOREIGN_PLACES = {
    "uae": "UAE", "u a e": "UAE", "emirates": "UAE", "usa": "USA", "u s a": "USA",
    "america": "USA", "uk": "UK", "u k": "UK", "england": "UK", "ksa": "KSA",
    "saudi": "Saudi Arabia", "saudi arabia": "Saudi Arabia",
}
for _name in ("Dubai, Sharjah, Abu Dhabi, Ajman, Jebel Ali, Muscat, Oman, Doha, Qatar, Riyadh, "
              "Jeddah, Dammam, Kuwait, Bahrain, Nepal, Kathmandu, Birgunj, Biratnagar, "
              "Bangladesh, Dhaka, Chittagong, Sri Lanka, Colombo, Bhutan, Thimphu, Maldives, "
              "Singapore, Malaysia, Kenya, Nairobi, Mombasa, Tanzania, Dar es Salaam, Nigeria, "
              "Lagos, South Africa, Durban, Uganda, Ethiopia, Egypt, Vietnam, Indonesia, "
              "Thailand, Australia, Canada, Germany").split(", "):
    FOREIGN_PLACES[_name.lower()] = _name

# country names that are also everyday words or products ("china clay", "mali")
COUNTRY_CLASHES = words("china chad mali togo niger guinea jordan georgia turkey dominica "
                        "jersey guernsey bermuda")
COUNTRIES = set()   # filled when the place list is built

# Devanagari trade words buyers mix in ("जंबो बैग 500 pcs")
DEVANAGARI = {"जंबो": "jumbo", "जम्बो": "jumbo", "बैग": "bag", "बेग": "bag", "बोरी": "bori",
              "बोरा": "bora", "कट्टा": "katta", "डिब्बा": "dabba", "डिब्बे": "dabbe",
              "बॉक्स": "box", "बोक्स": "box", "कार्टन": "carton", "फिल्म": "film",
              "श्रिंक": "shrink", "स्ट्रेच": "stretch", "सीमेंट": "cement", "बीओपीपी": "bopp",
              "हजार": "hazaar", "हज़ार": "hazaar", "लाख": "lakh", "करोड़": "crore",
              "किलो": "kg", "टन": "ton", "पीस": "pcs", "नग": "nag", "चाहिए": "chahiye",
              "माइक्रोन": "micron", "जीएसएम": "gsm"}


# ======================================================================================
# 2. Place list (built once, then cached)
# ======================================================================================

def to_ascii(s):
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().strip()


def drops_one_vowel(short, full):
    """True for typo-like aliases: "Hydrabad" is "Hyderabad" minus one vowel."""
    short, full = short.lower(), full.lower()
    return len(full) == len(short) + 1 and any(
        full[i] in "aeiou" and full[:i] + full[i + 1:] == short for i in range(len(full)))


@lru_cache(maxsize=1)
def load_places():
    """Return (place key -> display name, place keys ordered by population)."""
    places, population = {}, {}

    def add(name, display, pop, min_len=3):
        key = " ".join(re.sub(r"[^a-z ]", " ", name.lower()).split())
        if len(key) < min_len or key in STOPWORDS or key in TYPO_VOCAB:
            return
        if key not in places or pop > population[key]:
            places[key], population[key] = display, pop

    try:
        import geonamescache   # offline city database, pinned in requirements.txt
        gc = geonamescache.GeonamesCache()
        cities = gc.get_cities().values()
        for city in cities:   # Indian cities and their latin-script aliases
            if city["countrycode"] != "IN":
                continue
            name, pop = to_ascii(city["name"]), int(city.get("population") or 0)
            add(name, name, pop)
            for alt in city.get("alternatenames", []):
                if to_ascii(alt) == alt and re.fullmatch(r"[A-Za-z][A-Za-z .'-]+", alt):
                    # "Ahmdabad" is a typo -> show "Ahmedabad"; "Baroda" is a real name
                    add(alt, name if drops_one_vowel(alt, name) else alt, pop, min_len=5)
        for country in gc.get_countries().values():
            name = to_ascii(country["name"])
            if name.lower() not in COUNTRY_CLASHES:
                add(name, name, 0)
                COUNTRIES.add(name.lower())
        for city in cities:   # big foreign cities that can't clash with an Indian place
            name = to_ascii(city["name"])
            if city["countrycode"] != "IN" and city["population"] >= 1_000_000 \
                    and len(name) >= 5 and name.lower() not in places:
                add(name, name, 1)
    except Exception:   # library missing -> hand lists only
        pass

    for name in EXTRA_PLACES.replace("\n", " ").split(","):
        if name.strip():
            add(name.strip(), name.strip(), 10**9)
    for alias, display in FOREIGN_PLACES.items():
        add(alias, display, 10**8, min_len=2)
        if display.lower() in COUNTRIES or display in {"UAE", "USA", "UK", "KSA"}:
            COUNTRIES.add(display.lower())
    return places, sorted(places, key=lambda k: -population[k])


@lru_cache(maxsize=1)
def devanagari_cities():
    out = {}
    try:
        import geonamescache
        for city in geonamescache.GeonamesCache().get_cities().values():
            if city["countrycode"] == "IN":
                for alt in city.get("alternatenames", []):
                    if re.fullmatch(r"[ऀ-ॿ]+", alt):
                        out.setdefault(alt, to_ascii(city["name"]))
    except Exception:
        pass
    return out


# ======================================================================================
# 3. Clean the text and split it into tokens
# ======================================================================================

def clean_text(text):
    t = unicodedata.normalize("NFKC", text)
    for hindi, roman in DEVANAGARI.items():
        t = t.replace(hindi, f" {roman} ")
    if re.search(r"[ऀ-ॿ]", t):   # Devanagari city names -> latin ("इंदौर" -> Indore)
        t = re.sub(r"[ऀ-ॿ]+", lambda m: f" {devanagari_cities().get(m.group(), m.group())} ", t)
    for old, new in {"₹": " rs ", "µ": " micron ", "μ": " micron ", "×": " x ", "*": " x ",
                     "’": "'", "–": "-"}.items():
        t = t.replace(old, new)
    t = t.lower()
    for pattern, new in [
        (r"\bb\s*\.\s*o\s*\.\s*p\s*\.\s*p\b\.?", " bopp "),     # b.o.p.p
        (r"\bf\s*\.\s*i\s*\.\s*b\s*\.\s*c\b\.?", " fibc "),     # f.i.b.c
        (r"\bad\s*[-_]?\s*star\b", " adstar "),                 # ad-star
        (r"\bu\s*\.\s*a\s*\.\s*e\b\.?", " uae "),
        (r"\bu\s*\.\s*s\s*\.\s*a\b\.?", " usa "),
        (r"\bu\s*\.\s*k\b\.?", " uk "),
        (r"\bm\s*\.\s*t\b\.?|\bmetric\s*-?\s*ton(ne)?s?\b", " mt "),   # M.T., metric ton
        (r"\brs\s*\.", " rs "),
        (r"g\s*/\s*m\s*(2|²|sq)", " gsm "),                     # g/m2
        (r"\bgms?\s*/\s*sq\.?\s*m(tr|eter|etre)?\b", " gsm "),  # gm/sq mtr
        (r"/-", " /- "),                                        # Rs 500/-
    ]:
        t = re.sub(pattern, new, t)
    # 1,50,000 or 150,000 -> 150000
    t = re.sub(r"\b\d{1,3}(?:,\d{2})*,\d{3}\b", lambda m: m.group().replace(",", ""), t)
    t = re.sub(r"(\d)([a-z\"'%])", r"\1 \2", t)   # 65gsm -> 65 gsm, 15k -> 15 k
    t = re.sub(r"([a-z])(\d)", r"\1 \2", t)       # x24 -> x 24
    return t


@lru_cache(maxsize=4096)
def fix_typo(tok):
    if tok in SHORT_TYPOS:
        return SHORT_TYPOS[tok]
    if len(tok) < 4 or tok in STOPWORDS or tok in MULTIPLIERS or tok in WORD_NUMBERS \
            or tok in load_places()[0]:
        return tok
    match = difflib.get_close_matches(tok, TYPO_VOCAB, n=1, cutoff=0.8 if len(tok) >= 6 else 0.75)
    return match[0] if match else tok


def tokenise(text):
    toks = re.findall(r"\d+(?:\.\d+)?|[a-z]+|/-|[^\s\w]", clean_text(text))
    return [fix_typo(t) if t.isalpha() else t for t in toks]


def is_num(tok):
    return re.fullmatch(r"\d+(?:\.\d+)?", tok) is not None


# ======================================================================================
# 4. Find every number ("40 hazaar", "dedh lakh", "1 lakh 50 hazaar")
# ======================================================================================

def read_number(toks, i):
    """Read a number starting at toks[i]. Returns (value, end index, used multiplier)."""
    n = len(toks)

    def plain(j):   # 40, dedh, sadhe teen, twenty five
        if j >= n:
            return None
        tok = toks[j]
        if is_num(tok):
            return float(tok), j + 1
        if tok in FRACTIONS:
            return FRACTIONS[tok], j + 1
        if tok in ADD_TO_NEXT:
            nxt = plain(j + 1)
            if nxt:
                return nxt[0] + ADD_TO_NEXT[tok], nxt[1]
            return (1.25, j + 1) if tok in SAVA else None   # "sava lakh"
        if tok in WORD_NUMBERS:
            value, k = WORD_NUMBERS[tok], j + 1
            if k < n and toks[k] in WORD_NUMBERS and WORD_NUMBERS[toks[k]] < 10 <= value:
                value, k = value + WORD_NUMBERS[toks[k]], k + 1   # "twenty five"
            return float(value), k
        return None

    def with_multiplier(j):   # 40 hazaar, 2.5 lac -> (value, end, last multiplier or 0)
        p = plain(j)
        if not p:
            return None
        value, k = p
        mult = 0
        while k < n and toks[k] in MULTIPLIERS:
            mult = MULTIPLIERS[toks[k]]
            value *= mult
            k += 1
        return value, k, mult

    first = with_multiplier(i)
    if not first:
        return None
    value, k, mult = first
    if not is_num(toks[i]) and not mult:
        # a number word alone is trusted right before a weight ("half ton", "dedh ton",
        # "two tonnes"), or in English before a counted unit ("ten jumbo bags")
        weight_next = k < n and toks[k] in KG_WORDS | TON_WORDS | QUINTAL_WORDS
        english_count = toks[i] in ENGLISH_NUMBERS and set(toks[k:k + 3]) & (PCS_WORDS | ROLL_WORDS)
        if not (weight_next or english_count):
            return None
    used = mult > 0
    while used and k < n:   # "1 lakh 50 hazaar": keep adding smaller groups
        nxt = with_multiplier(k)
        if not nxt or nxt[2] == 0 or nxt[2] >= mult:
            break
        value, k, mult = value + nxt[0], nxt[1], nxt[2]
    return value, k, used


def find_numbers(toks):
    numbers, i = [], 0
    while i < len(toks):
        r = read_number(toks, i)
        if r:
            numbers.append({"value": r[0], "start": i, "end": r[1], "mult": r[2],
                            "raw": toks[i], "kind": None})
            i = r[1]
        else:
            i += 1
    return numbers


# ======================================================================================
# 5. Decide what each number is
# ======================================================================================

def label_number(m, toks, places):
    s, e, n = m["start"], m["end"], len(toks)
    at = lambda i: toks[i] if 0 <= i < n else ""   # noqa: E731
    prv, prv2, nxt, nxt2, nxt3 = at(s - 1), at(s - 2), at(e), at(e + 1), at(e + 2)
    digits = m["raw"].replace(".", "") if is_num(m["raw"]) else ""
    m["after_product"] = prv in PCS_WORDS   # "box 2000", "bags 40000"

    def owns(tok):   # "25 mic": a small number right before the keyword already owns it
        return is_num(tok) and float(tok) < 1000   # ...but "40000 gsm 65" doesn't

    def keyword_before(kw):
        """"gsm 65", "gsm: 65" -- but not the 500 in "25 mic 500 kg" (mic belongs to 25)."""
        if nxt in UNIT_WORDS:
            return False
        if prv in kw:
            return not owns(prv2)
        return prv in {":", "-", "=", "of"} and prv2 in kw and not owns(at(s - 3))

    def kind():
        spec_ok = not m["mult"] and m["value"] < 1000   # "50 thousand" is never a gsm
        if spec_ok and (nxt in GSM_WORDS or keyword_before({"gsm"})):
            return "gsm"
        if spec_ok and (nxt in MICRON_WORDS or keyword_before(MICRON_WORDS)):
            return "micron"
        if spec_ok and (nxt in GAUGE_WORDS or keyword_before(GAUGE_WORDS)):
            return "gauge"
        if (not m["mult"] and len(digits) >= 10) or prv in PHONE_BEFORE:
            return "phone"
        # "price of 1000 bopp bags" asks the price OF 1000 bags: a count, not a price
        counted = nxt in PCS_WORDS | ROLL_WORDS | PRODUCT_WORDS and m["value"] == int(m["value"])
        price_word = prv in {"price", "rate", "budget", "cost"} or keyword_before({"price", "rate"})
        if (prv in PRICE_BEFORE and not price_word) or keyword_before({"rs", "inr"}) \
                or (price_word and not counted) \
                or (nxt in PRICE_AFTER and not (nxt == "/-" and nxt2 in PCS_WORDS)) \
                or (nxt == "/" and nxt2 in KG_WORDS | PCS_WORDS | ROLL_WORDS) \
                or (nxt == "per" and nxt2 not in PER_PERIOD | {"order", "lot"}):
            return "price"   # Rs 6.50, 120/kg, 6 per bag
        if (nxt in {"x", "by"} and is_num(nxt2)) or (prv in {"x", "by"} and is_num(prv2)) \
                or nxt in SIZE_WORDS:
            return "size"    # 18 x 24, 500 mm
        if nxt in SPEC_WORDS:
            return "spec"    # 5 ply, 4 colour, 2 ml
        if nxt in TIME_WORDS and prv not in {"per", "a", "every", "/"}:
            return "time"    # 15 din, 2 weeks
        if prv in PIN_BEFORE or (prv == ":" and prv2 in {"pin", "pincode", "code"}):
            return "pincode"
        if nxt in KG_WORDS | TON_WORDS | QUINTAL_WORDS:
            m["kg"] = m["value"] * (1000 if nxt in TON_WORDS else 100 if nxt in QUINTAL_WORDS else 1)
            m["wunit"] = "mt" if nxt in TON_WORDS else "kg"
            # "25 kg bag", "capacity 1000 kg", "50 kg / bag" describe one bag, not the order
            after = set() if nxt2 in {"per", "a", "every"} and nxt3 in PER_PERIOD else {nxt2, nxt3}
            if prv in CAPACITY_WORDS or prv2 in {"capacity", "cap", "load", "swl"} \
                    or after & FILL_AFTER \
                    or (nxt2 == "/" and nxt3 in {"bag", "roll", "box", "pc", "pcs"}):
                return "capacity"
            return "weight"
        if nxt in ROLL_WORDS or (nxt in PCS_WORDS - {"bags", "bag", "boxes", "box"}
                                 and prv in ROLL_WORDS):
            return "qty_roll"   # 200 rolls
        if nxt in PCS_WORDS:
            return "qty_pcs"    # 5000 pcs, 500 bags
        if prv in QTY_BEFORE or keyword_before({"qty", "quantity", "qnty", "moq"}):
            return "qty_bare"   # qty 5000
        if not m["mult"] and len(digits) == 6 and m["raw"][0] in "12345678" \
                and not m["raw"].endswith("000") and (prv in places or nxt in places):
            return "pincode"    # "Nagpur 440001" (but "Nagpur 150000" is a quantity)
        return "bare"

    m["kind"] = kind()


def link_ranges(numbers, toks):
    """"65-70 gsm", "5 to 10 thousand pcs": the first number takes the second's meaning."""
    for a, b in zip(numbers, numbers[1:]):
        between = toks[a["end"]:b["start"]]
        if not (len(between) == 1 and between[0] in RANGE_WORDS and a["kind"] == "bare"):
            continue
        a["kind"] = b["kind"]
        if "kg" in b:
            a["kg"] = a["value"] * (b["kg"] / b["value"] if b["value"] else 1)
            a["wunit"] = b["wunit"]
        if not a["mult"] and b["mult"] and a["value"] < b["value"]:
            scale = 1   # "5-10 lakh" -> 5 lakh
            for w in toks[b["start"]:b["end"]]:
                scale *= MULTIPLIERS.get(w, 1)
            if a["value"] * scale <= b["value"]:
                a["value"] *= scale
                a["mult"] = True
                if "kg" in a:
                    a["kg"] *= scale
        b["kind"] += "_rangeend"


# ======================================================================================
# 6. Category
# ======================================================================================

NOT = r"(?:non|no|without|bina|w/o|un|not)"
BAG = r"\b(bags?|sacks?|bori|bora|boriya|kattas?|thaila|thaile|thaili)\b"
FILM = r"\b(films?|shrink|stretch|cling|pof|wrap|wrapping|stretchfilm|shrinkfilm)\b"
POUCH = (r"\bpouch(es|s)?\b|\bstand\s*-?\s*up\b|\bstandy\b|\bdoy\s*-?\s*packs?\b|"
         r"\bzip\s*-?\s*lock\b|\bziplock\b|\bzipper\b|\bsachets?\b|\bspouted\b|"
         r"\b(three|3|center|centre)\s*side\s*seal\b|\bcent(er|re)\s*seal\b")
MONO = (r"\bmono\s*-?\s*(cartons?|cartoons?|box|boxes)\b|\bmonocart\w*|"
        r"\bduplex\b|\bfolding\s*(cartons?|box|boxes)\b|\b(fbb|sbs)\b")

# product families: if a message names two, the first one mentioned wins
FAMILIES = {
    "pouch": POUCH,
    "bag": r"\b(bags?|sacks?|bori|bora|boriya|kattas?|fibc|woven|adstar|cement)\b|"
           r"\bjumbo\b(?!\s*(rolls?|films?))",
    "film": r"\b(films?|shrink|stretch|cling|pof|wrap|wrapping)\b",
    "box": r"\b(corrugated|corugated|cartons?|cartoons?|box|boxes|dabba|dabbe|peti|ply|duplex)\b",
}


def find_category(text, numbers):
    t = " ".join(text)
    if re.search(r"\bnon\s*-?\s*woven\b|\bnonwoven\b", t):   # non-woven isn't a category
        t = re.sub(r"\bnon\s*-?\s*woven\b|\bnonwoven\b", " nonwoven ", t)
        if not re.search(r"\bbopp|\bjumbo\b|\bfibc\b|\bfilm|\bbox|\bcarton|\bcement\b|\bsacks?\b", t):
            return None
    first = {f: m.start() for f, p in FAMILIES.items() if (m := re.search(p, t))}
    lead = min(first, key=first.get) if first else None
    if len(first) > 1:   # "5 ply box and stretch film" -> hide everything but the box
        for family, pattern in FAMILIES.items():
            if family != lead:
                t = re.sub(pattern, " ", t)
    # "box 500, bopp bags 2000" / "corrugated box with bopp lamination": a box comes first,
    # so a later "bopp" or "laminated" is not the product
    bopp = re.search(r"\bbopp\w*|\bboop\b", t)
    if lead == "box" and bopp and bopp.start() > first["box"]:
        t = re.sub(r"\bbopp\w*|\bboop\b|\blaminat\w*", " ", t)
    return category_rules(t, numbers)


def category_rules(t, numbers):
    """Checked top to bottom; the first rule that matches wins."""
    has = lambda p: re.search(p, t) is not None   # noqa: E731
    bag, film = has(BAG), has(FILM)
    heavy = any(m["kind"] == "capacity" and m.get("kg", 0) >= 500 for m in numbers)

    # jumbo / bulk / big bags -> fibc (but "jumbo roll" of film is not)
    if has(r"\bfibc\b|\bsuper\s*sacks?\b|\bbig\s*bags?\b|\bbulk\s*(bags?|sacks?)\b|"
           r"\bton(ne)?\s*bags?\b|\bu\s*panel\b|\bbaffle\b|\b4\s*loop\b|\bfour\s*loop\b"):
        return "fibc"
    if has(r"\bjumbo\b") and not has(r"\bjumbo\s*(rolls?|films?|size)\b"):
        return "fibc"
    # "laminated pouch", "bopp pouch" are pouches, not bags
    if has(POUCH):
        return "pouch"
    # cement bags and AD Star -> woven_sack
    if has(r"\badstar\b|\bcement\b|\bblock\s*bottom\b"):
        return "woven_sack"
    # shrink / stretch / cling -> always film
    if has(r"\b(shrink|stretch|cling|pof|stretchfilm|shrinkfilm)\b|\b(pallet|stretch)\s*wrap"):
        return "film"
    # bopp film -> film, bopp (laminated) bag -> bopp_bag
    if has(r"\bbopp?\b|\bboop\b|\bbopp\w*") and not has(NOT + r"[\s-]*bopp"):
        return "film" if film and not bag else "bopp_bag"
    if bag and has(r"\blaminat\w*") and not has(NOT + r"[\s-]*laminat|\bunlaminat"):
        return "bopp_bag"
    if film:
        return "film"
    # mono / duplex cartons, unless a corrugated product is named first
    mono = re.search(MONO, t)
    if mono:
        corr = re.search(r"\b(corrugated|corugated|shippers?|rsc|master\s*cartons?)\b|\b\d\s*ply\b", t)
        return "corrugated_box" if corr and corr.start() < mono.start() else "mono_carton"
    # a plain box / carton -> corrugated_box
    if has(r"\b(corrugated|corugated|corrugate|cartons?|cartoons?|box|boxes|dabba|dabbe|dabbas|"
           r"peti|shippers?|rsc)\b") or (has(r"\bply\b") and not bag):
        return "corrugated_box"
    if has(r"\bwoven\b|\bsacks?\b|\b(pp|hdpe|raffia|polypropylene)\s*(woven\s*)?(bags?|sacks?)\b|"
           r"\bbori\b|\bbora\b|\bboriya\b|\bkattas?\b|\bfertili[sz]er\s*bags?\b|\braffia\b"):
        return "woven_sack"
    # just "bags": a fabric gsm means woven sack, a 500 kg+ load means jumbo bag
    if bag and any(m["kind"] == "gsm" for m in numbers):
        return "woven_sack"
    if heavy:
        return "fibc" if bag or has(r"\bbags?\b") else None
    return None


# ======================================================================================
# 7. Quantity and unit
# ======================================================================================

def as_number(v):
    return int(round(v)) if abs(v - round(v)) < 1e-9 else round(v, 3)


def pick_quantity(numbers, category):
    of = lambda kind: [m for m in numbers if m["kind"] == kind]   # noqa: E731
    weights = of("weight")
    if category != "fibc":
        # only a jumbo bag holds tonnes, so "2 tonne woven sack" is the order itself
        weights = sorted(weights + [m for m in of("capacity") if m["wunit"] == "mt"],
                         key=lambda m: m["start"])
    if category in {"bopp_bag", "woven_sack", "corrugated_box", "pouch", "mono_carton"}:
        weights = [w for w in weights if w["kg"] > 100]           # "50 kg bags" = fill weight
    if category == "fibc":
        weights = [w for w in weights if not 250 <= w["kg"] <= 3000]   # one jumbo bag's load

    def weight(w):
        return (as_number(w["kg"] / 1000), "mt") if w["wunit"] == "mt" else (as_number(w["kg"]), "kg")

    bare = of("bare")
    counted = of("qty_pcs") or of("qty_roll") or of("qty_bare") or [m for m in bare if m["mult"]]
    if counted:                                   # 5000 pcs, 200 rolls, qty 5000, 40 hazaar
        return as_number(counted[0]["value"]), "pcs"
    if weights and (category == "film" or not bare):
        return weight(weights[0])                 # 500 kg shrink film, 3 mt
    if bare:
        # "corrugated box 2,000 + mono carton 5,000": a number right after the product
        # name belongs to it; otherwise take the largest (small ones tend to be sizes)
        named = [m for m in bare if m["after_product"] and m["value"] >= 100]
        best = named[0] if named else max(bare, key=lambda m: m["value"])
        return as_number(best["value"]), "pcs"
    if weights:
        return weight(weights[0])
    return None, None


# ======================================================================================
# 8. Location
# ======================================================================================

def find_location(toks, places, keys):
    n = len(toks)
    is_word = lambda i: 0 <= i < n and toks[i].isalpha()   # noqa: E731
    found = []   # (start, end, display name, fuzzy?)

    # exact match, longest first (up to 3 words); 2-3 letter names need a cue ("to UK")
    i = 0
    while i < n:
        for size in (3, 2, 1):
            key = " ".join(toks[i:i + size])
            if i + size <= n and all(is_word(j) for j in range(i, i + size)) and key in places \
                    and not (size == 1 and len(key) <= 3 and toks[max(i - 1, 0)] not in CUE_BEFORE):
                found.append((i, i + size, places[key], False))
                i += size
                break
        else:
            i += 1

    # fuzzy match for misspellings ("banglore"); strict, so villages don't become towns
    if not found:
        for i, tok in enumerate(toks):
            if not is_word(i) or len(tok) < 5 or tok in STOPWORDS or CHAHIYE.match(tok):
                continue
            pool = [k for k in keys if k[0] == tok[0] and abs(len(k) - len(tok)) <= 1]
            match = difflib.get_close_matches(tok, pool, n=1, cutoff=0.88)
            if match:
                found.append((i, i + 1, places[match[0]], True))
            elif is_word(i + 1):
                two = tok + " " + toks[i + 1]
                pool = [k for k in keys if " " in k and k[0] == two[0]]
                match = difflib.get_close_matches(two, pool, n=1, cutoff=0.9)
                if match:
                    found.append((i, i + 2, places[match[0]], True))

    # an unknown word that could still be a place name
    def could_be_place(i):
        tok = toks[i] if 0 <= i < n else ""
        return (is_word(i) and 3 <= len(tok) <= 20 and not CHAHIYE.match(tok)
                and tok not in STOPWORDS | GSM_WORDS | MICRON_WORDS | PCS_WORDS
                and tok not in MULTIPLIERS and tok not in WORD_NUMBERS
                and fix_typo(tok) not in TYPO_VOCAB)

    def title(ws):
        return " ".join(w.capitalize() for w in ws)

    def is_region(name):
        return name.lower() in STATES or name.lower() in COUNTRIES

    if found:
        def rank(f):
            s, e, name, fuzzy = f
            cue = toks[s - 1] in DELIVERY_CUES if s > 0 else False
            cue = cue or (e < n and toks[e] in DELIVERY_CUES)
            source = (s > 0 and toks[s - 1] in {"from", "frm"}) or (e < n and toks[e] == "se")
            # exact > fuzzy, city > state/country, not "from X", delivery cue, earliest
            return (not fuzzy, not is_region(name), not source, cue, -s)
        best = max(found, key=rank)
        if is_region(best[2]):
            # "to Port Louis, Mauritius" / "at Sinnar, Maharashtra": an unknown town after
            # a delivery cue, right before the state or country, is the better answer
            j = best[0] - 1 - (best[0] > 0 and toks[best[0] - 1] == ",")
            town = []
            while j >= 0 and len(town) < 2 and could_be_place(j):
                town.insert(0, toks[j])
                j -= 1
            if town and j >= 0 and toks[j] in CUE_BEFORE - {"from", "frm"}:
                return title(town)
        return best[2]

    # unknown place: a word after a cue ("at Sinnar") or before one ("Sinnar mein")
    for i in range(n):
        if toks[i] in CUE_BEFORE - {"from", "frm"} and could_be_place(i + 1):
            return title(toks[i + 1:i + 3] if could_be_place(i + 2) else toks[i + 1:i + 2])
        if toks[i] in CUE_AFTER and could_be_place(i - 1):
            return title(toks[i - 2:i] if i >= 2 and could_be_place(i - 2) else toks[i - 1:i])

    # last comma-separated piece made of 1-2 unknown words ("..., Bhusawal")
    chunks, cur = [], []
    for tok in toks + [","]:
        if tok in {",", ";", "-", ".", "|", "\n", "/"}:
            if cur:
                chunks.append(cur)
            cur = []
        else:
            cur.append(tok)
    for chunk in reversed(chunks):
        if 1 <= len(chunk) <= 2 and all(t.isalpha() for t in chunk):
            starts = [i for i in range(n - len(chunk) + 1) if toks[i:i + len(chunk)] == chunk]
            if starts and all(could_be_place(starts[-1] + k) for k in range(len(chunk))):
                return title(chunk)
    return None


# ======================================================================================
# Public function
# ======================================================================================

def parse_rfq(text: str) -> dict:
    result = {"category": None, "quantity": None, "unit": None,
              "gsm": None, "micron": None, "location": None}
    if not isinstance(text, str) or not text.strip():
        return result

    places, keys = load_places()
    toks = tokenise(text)
    numbers = find_numbers(toks)
    for m in numbers:
        label_number(m, toks, places)
    link_ranges(numbers, toks)

    category = find_category(toks, numbers)
    if category == "pouch":   # "500 gm pouch" is a fill weight; only a literal "gsm" counts
        for m in numbers:
            if m["kind"] == "gsm" and m["end"] < len(toks) and toks[m["end"]] != "gsm":
                m["kind"] = "spec"
    quantity, unit = pick_quantity(numbers, category)

    first = lambda kind: next((m["value"] for m in numbers if m["kind"] == kind), None)  # noqa: E731
    gsm, micron = first("gsm"), first("micron")
    if micron is None and first("gauge") is not None:
        micron = first("gauge") / 4   # 100 gauge = 25 micron

    result.update(category=category, quantity=quantity, unit=unit,
                  gsm=None if gsm is None else int(round(gsm)),
                  micron=None if micron is None else int(round(micron)),
                  location=find_location(toks, places, keys))
    return result


if __name__ == "__main__":
    import json
    import sys

    for line in sys.argv[1:] or ["bopp bags chahiye 40 hazaar, 65 gsm, Nagpur",
                                 "jumbo bag 1000 kg capacity, 500 pcs, Vadodara"]:
        print(json.dumps(parse_rfq(line), ensure_ascii=False))
