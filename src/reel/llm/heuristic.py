"""An offline, rule-based script -> spec planner.  No model, no network: ``reel build script.txt`` works
on a plane.

The planner reads a script the way a storyboard artist skims it:

1. **parse**  - title line, ``Name:`` dialogue lines, "quoted" speech with its attribution, stage
   directions in (parentheses), plain narration; every sentence becomes a *unit* (one caption);
2. **cast**   - characters from names, ``Narrator:`` prefixes, role nouns and pronouns (max 3), an
   archetype guessed from words like kid / grandma / robot / boss / hero (default everyman) and
   distinct, stable colours;
3. **plan**   - units are grouped into 8-14 scenes whose lengths come from reading time (~2.6 words/s
   + 1 s); a short script is padded with silent *bridge* scenes, a long one is condensed to its most
   salient sentences; the first scene gets a ``title`` caption, the last exclamation a ``shout``;
4. **stage**  - per scene: a background chosen from keywords (only templates that exist in the
   catalog; its own enum params such as ``room: kitchen`` are filled from the text), time of day and
   mood, characters on universal slots with ``enter_from`` entrances, actions from verbs (walk, ran,
   jump, wave, laughed, thought, pointed, fell, picked up, gasped ...), listeners looking at the
   speaker, sfx from the actions, a slow camera move, a rotating transition;
5. **fit**    - the shared :func:`reel.llm.generator.normalize_spec` rescales the scene lengths to the
   45-60 s budget exactly, and the linter has the last word.

Everything is catalog-driven: names are looked up in the registries at call time and anything that
does not exist is simply not used.  The output is a pure function of ``(script, style, seed,
target_duration, catalog)``: no global random state, no ``hash()``.
"""

from __future__ import annotations

import itertools
import math
import random
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass, field, replace
from typing import Any

from pydantic import BaseModel, ValidationError

from reel.assets.model import ARMS_ONLY
from reel.assets.sprite import PERSON_HEIGHT
from reel.core.catalog import CATALOG, Catalog
from reel.core.lint import LintOptions, lint_data
from reel.core.rng import stable_int
from reel.llm.base import (
    CHARACTER_PALETTES,
    MAX_ON_SCREEN,
    MAX_SCENE_SEC,
    MAX_SCENES,
    MIN_SCENE_SEC,
    MIN_SCENES,
    READ_WPS,
    SKIN_TONES,
    GenerationResult,
    SpecGenerationError,
    SpecGenerator,
    clamp_target,
    default_audio,
    guess_archetype,
)
from reel.llm.generator import normalize_spec
from reel.llm.library_plan import ENGINE_PLACE_WORDS, LibraryPlanner, attach_library_gaps
from reel.llm.props_plan import PropTracker
from reel.llm.stagecraft import (
    PLURAL_WORDS,
    SLOT_X,
    ActionRequest,
    Cast,
    CatalogView,
    Char,
    Cue,
    bridge_requests,
    clean_text,
    cut_value,
    enter_request,
    extract_cues,
    flavor_cue,
    flourish_request,
    listener_request,
    mentions,
    plan_camera,
    plan_cue,
    plan_sfx,
    plan_transition,
    schedule_requests,
)


# =============================================================================== lexicons
def _w(text: str) -> list[str]:
    """A table of words written as one whitespace-separated string."""
    return text.split()


_STOP = frozenset(
    _w(
        """
        a an the this that these those it its he she they we you i me my mine our ours your yours
        his her hers their theirs and but or nor so yet for if when while as after before once until
        unless though although because since than then now here there one two three four five six
        seven eight nine ten first second third next last every each some many most all any no not
        none why how what who whom whose where which whatever let lets please today tonight tomorrow
        yesterday suddenly finally meanwhile soon later also even just still maybe perhaps sometimes
        always never often in on at by from with without within to of into onto over under behind
        beside near inside outside around through across up down off out everyone everybody nobody
        nothing something somewhere anything everything someone somebody anyone yes okay ok hello hi
        hey oh ah wow well sweet good great nice mr mrs ms dr sir monday tuesday wednesday thursday
        friday saturday sunday january february march april may june july august september october
        november december chapter scene act part title narrator voiceover
        """
    )
)
_NOT_SPEAKER = frozenset(
    _w(
        """
        title scene note notes setting int ext time date location caption captions summary hint tip
        tips step steps chapter act part example examples fact facts rule rules idea question answer
        warning remember reminder tldr topic subject script cut fade
        """
    )
)
_NARRATOR_NAMES = frozenset(
    _w(
        """
        narrator voiceover voice vo announcer host presenter speaker storyteller
        """
    )
)
_FEMALE = frozenset(
    _w(
        """
        mia emma olivia sophia sophie lily anna sara sarah maya zoe ella ava nora ruby grace chloe
        amy emily lucy lila luna nina rosa rose julia kate katie laura eva eve ivy jane jill kim
        lena lisa mary meera priya riya diya ananya kavya neha pooja anjali sana aisha fatima leila
        maria sofia isla amelia mom mum mother mama grandma granny grandmother nana sister aunt girl
        sita gita geeta radha sunita anita kavita rani lata asha usha seema nisha ritu tara ira aditi
        ishita anu ria shreya swati rekha sneha tanvi pari mira meena zoya sapna komal simran preeti
        woman queen lady princess she her hers herself
        """
    )
)
_MALE = frozenset(
    _w(
        """
        tom jack leo max sam ben noah liam oliver jake dan david john james peter paul mark luke
        adam ethan lucas henry oscar theo raj ravi arjun rohit rahul aarav aditya ishaan kabir
        vihaan amit sanjay rohan vikram karan arnav dev krish kunal manish mohan nikhil ramesh suresh
        ramu raju gopal kishan pranav rishi sachin vijay yash ayaan shyam govind ganesh
        dad father papa grandpa grandfather uncle brother boy man king prince he
        him his himself
        """
    )
)
_PRONOUNS = {
    "he": "m", "him": "m", "his": "m", "himself": "m",
    "she": "f", "her": "f", "hers": "f", "herself": "f",
    "they": "p", "them": "p", "their": "p", "theirs": "p",
}  # fmt: skip
_PLACE_PREPOSITIONS = frozenset(_w("in at from near into inside outside through across around"))
_POSSESS = frozenset(
    _w("with holding holds carrying carries carried wearing wears wore has had having held")
)
_SPEECH_VERBS = frozenset(
    _w(
        """
        said says asked asks replied replies shouted shouts whispered whispers cried cries exclaimed
        exclaims called calls yelled yells answered answers added adds muttered mutters laughed
        laughs sighed sighs gasped gasps announced announces declared declares told tells
        """
    )
)

_ROLE_NOUNS: dict[str, str] = {
    "robot": "robot", "android": "robot", "boy": "kid", "girl": "kid", "kid": "kid",
    "child": "kid", "student": "kid", "teacher": "everyman", "boss": "boss",
    "manager": "boss", "hero": "hero", "knight": "hero", "man": "everyman",
    "woman": "everyman", "stranger": "everyman", "friend": "everyman", "neighbor": "everyman",
    "neighbour": "everyman", "farmer": "everyman", "chef": "everyman", "doctor": "everyman",
    "thief": "everyman", "king": "hero", "queen": "hero", "wizard": "elder",
    # more roles a story brings in: the body that fits until the library draws them
    "hunter": "everyman", "villager": "everyman", "sailor": "everyman", "fisherman": "everyman",
    "gardener": "everyman", "baker": "everyman", "postman": "everyman", "painter": "everyman",
    "singer": "everyman", "dancer": "everyman", "musician": "everyman", "driver": "everyman",
    "waiter": "everyman", "customer": "everyman", "passenger": "everyman", "traveler": "everyman",
    "traveller": "everyman", "tourist": "everyman", "butcher": "everyman", "tailor": "everyman",
    "potter": "everyman", "carpenter": "everyman", "miner": "everyman", "owner": "everyman",
    "guard": "everyman", "servant": "everyman", "beggar": "everyman", "merchant": "everyman",
    "vendor": "everyman", "barber": "everyman", "mechanic": "everyman", "nurse": "everyman",
    "robber": "everyman", "prisoner": "everyman", "mother": "everyman", "father": "everyman",
    "dad": "everyman", "mom": "everyman", "mum": "everyman", "uncle": "everyman", "aunt": "everyman",
    "brother": "everyman", "sister": "everyman", "astronaut": "hero", "pirate": "hero",
    "prince": "hero", "princess": "hero", "captain": "hero", "pilot": "hero", "warrior": "hero",
    "ninja": "hero", "superhero": "hero", "baby": "kid", "toddler": "kid", "schoolboy": "kid",
    "schoolgirl": "kid", "grandfather": "elder", "grandpa": "elder", "witch": "elder", "monk": "elder",
}  # fmt: skip
_ARTICLES = r"(?:the|a|an|this|that|our|my|his|her|their)"
#: the first word after an article, and (not consumed) the two after it: "a thirsty crow and ...", "the very hungry old lion"
_ROLE_RE = re.compile(rf"\b{_ARTICLES}\s+([\w'’-]+)(?=((?:\s+[\w'’-]+){{0,2}}))", re.I)
#: a noun phrase ends here: what comes after belongs to the next one
_PHRASE_END = frozenset(
    _w(
        "the a an this that our my his her their and or but of in on at to with from by for named called who which"
    )
)


def _guest_nouns(chunk: str, known: set[str]) -> list[str]:
    """The role nouns that open a clause as its subject ("An elephant arrived", "... and the tiger watched"), as :func:`_role_nouns`
    reads them."""
    out: list[str] = []
    for m in _GUEST_RE.finditer(chunk):
        for word in [m.group(1), *m.group(2).split()]:
            w = _POSSESSIVE.sub("", word.lower())
            if w in known:
                out.append(w)
                break
            if w in _PHRASE_END:
                break
    return out


def _role_nouns(chunk: str, known: set[str]) -> list[str]:
    """The role noun of each noun phrase after an article, whatever adjectives stand in front of it ("the dog", "a little boy",
    "a thirsty crow", "the very hungry old lion"): the first word of up to three that the cast builder knows."""
    out: list[str] = []
    for m in _ROLE_RE.finditer(chunk):
        for word in [m.group(1), *m.group(2).split()]:
            w = _POSSESSIVE.sub("", word.lower())
            if w in known:
                out.append(w)
                break
            if w in _PHRASE_END:
                break
    return out


_PROP_WORDS = {
    "umbrella": "umbrella", "phone": "phone", "smartphone": "phone", "book": "book",
    "books": "book", "coffee": "coffee", "hat": "hat", "cap": "cap", "glasses": "glasses",
    "backpack": "backpack", "briefcase": "briefcase", "balloon": "balloon", "balloons": "balloon",
    "flower": "flower", "flowers": "flower", "scarf": "scarf",
}  # fmt: skip

#: time of day -> (strong cues, weak cues); weak ones ("night", "sleep") only set the scene in stories
_TIME_WORDS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "night": (
        ("midnight", "moon", "moonlight", "stars", "starry", "bedtime", "flashlight", "torch",
         "lantern", "nightfall"),
        ("night", "dark", "darkness", "shadow", "shadows", "asleep", "sleep"),
    ),
    "dusk": (("evening", "sunset", "dusk", "twilight"), ()),
    "dawn": (("morning", "sunrise", "dawn", "breakfast"), ()),
    "day": (("noon", "afternoon", "sunny", "daytime", "lunch"), ()),
}  # fmt: skip
_MOOD_BG: dict[str, tuple[str, ...]] = {
    "gloomy": ("sad", "cry", "lonely", "gloomy", "rain", "storm", "lost", "empty", "alone", "grey"),
    "playful": ("happy", "joy", "laugh", "party", "celebrate", "fun", "dance", "play", "silly"),
    "dramatic": ("scary", "spooky", "monster", "ghost", "shadow", "danger", "mystery", "stolen",
                 "stole", "thief", "chase", "dark", "midnight", "twist"),
    "cool": ("cold", "winter", "snow", "ocean", "sea", "ice", "frozen", "calm"),
    "warm": ("warm", "cozy", "cosy", "home", "kitchen", "cookie", "cookies", "family", "love",
             "sunrise", "sunset", "evening"),
}  # fmt: skip
#: synonyms for backgrounds the planner knows by name; only used for templates that exist
_BG_KEYWORDS: dict[str, tuple[str, ...]] = {
    "room": ("room", "kitchen", "bedroom", "office", "house", "home", "apartment", "living",
             "sofa", "couch", "bed", "desk", "fridge", "bathroom", "indoors"),
    "street": ("street", "city", "road", "shop", "shops", "store", "market", "town", "sidewalk",
               "traffic", "cars", "bus", "cafe", "downtown", "avenue", "crosswalk"),
    "forest": ("forest", "trees", "tree", "woods", "park", "jungle", "garden", "bushes", "bush",
               "hedge", "meadow", "hill", "hills", "mountain", "trail", "camp", "lake", "river",
               "grass", "picnic", "outdoors", "nature"),
    "rooftop": ("roof", "rooftop", "skyline", "skyscraper", "terrace", "balcony"),
    "whiteboard": ("board", "whiteboard", "explain", "explains", "graph", "chart", "idea", "data",
                   "diagram", "lesson", "teach", "learn", "science", "brain", "math", "formula",
                   "slide", "presentation", "notes", "memory", "memories"),
    "stage": ("stage", "show", "spotlight", "audience", "theater", "theatre", "concert",
              "perform", "performance", "curtain", "applause", "microphone", "sing", "song",
              "band"),
}  # fmt: skip
_PLACE_WORDS = frozenset(w for words in _BG_KEYWORDS.values() for w in words if " " not in w)
#: words that turn up in any story without saying where it happens ("she sang a song", "at her desk", "an idea"): half a point of
#: evidence for the place, so one of them never moves the story to another set
_WEAK_BG: dict[str, frozenset[str]] = {
    "room": frozenset(_w("desk sofa couch bed fridge living indoors")),
    "street": frozenset(_w("shop shops store market cafe bus cars")),
    "forest": frozenset(_w("tree trees grass bushes bush hedge hill hills garden camp picnic meadow lake river nature outdoors")),
    "whiteboard": frozenset(_w("board explain explains graph chart idea data diagram lesson teach learn science brain math formula slide presentation notes memory memories")),
    "stage": frozenset(_w("show sing song band microphone perform performance applause")),
}  # fmt: skip
#: where the library's animals live, when no scene names a place and nobody has a trade: a story of animals is in the woods (the
#: farm animals on the farm), not on a bare gradient
_HABITAT: dict[str, str] = dict.fromkeys(
    _w(
        """
        bird crow parrot peacock deer elephant fox lion tiger monkey rabbit rat tortoise dinosaur
        dragon fish dog cat
        """
    ),
    "forest",
) | {"cow": "farm", "horse": "farm"}
#: what somebody's trade says about where the story is, when no scene names a place: archetype -> background
_ROLE_SETTING: dict[str, str] = {
    "farmer": "farm", "teacher": "classroom", "student_boy": "classroom", "student_girl": "classroom",
    "doctor": "hospital", "chef": "cafe", "shopkeeper": "market", "boss": "office",
}  # fmt: skip
#: a cast whose tallest member is shorter than this (design px; a person is 575) is drawn bigger, by up to SMALL_CAST_GROWS
SMALL_CAST = 330.0
SMALL_CAST_GROWS = 1.3
#: a scene leaves the set it is on only for evidence worth this much (a place named once is 3 points, a word that merely hints 1)
SWITCH_SCORE = 2.0
#: ... and a place spoken about but not told ("I forgot my book at home") counts for this share of what the same words would in narration
SPEECH_SHARE = 0.4


def engine_vocabulary() -> list[tuple[str, str, tuple[str, ...]]]:
    """``(name, kind, words)`` for what the engine itself draws and the planner recognises by word: its backgrounds,
    its body archetypes and its props.  (A role like "doctor" or "farmer" is drawn as an everyman, which is a stand-in,
    not the thing: it is not listed.)"""
    from reel.llm.base import ARCHETYPE_WORDS

    out: list[tuple[str, str, tuple[str, ...]]] = [
        (name, "place", words) for name, words in _BG_KEYWORDS.items()
    ]
    out += [
        (name, "place", words) for name, words in ENGINE_PLACE_WORDS.items()
    ]  # in Hindi and Hinglish
    out += [
        (name, "character", tuple(w for w, weight in table.items() if weight >= 2))
        for name, table in ARCHETYPE_WORDS.items()
    ]
    out.append(("everyman", "character", _EVERYMAN_WORDS))
    props: dict[str, list[str]] = {}
    for word, prop in _PROP_WORDS.items():
        props.setdefault(prop, []).append(word)
    out += [(name, "object", tuple(words)) for name, words in props.items()]
    return out


_PALETTES = CHARACTER_PALETTES
_SKINS = SKIN_TONES
_ABBREV = ("Mr.", "Mrs.", "Ms.", "Dr.", "Prof.", "St.", "vs.", "e.g.", "i.e.", "etc.")
#: background params the planner fills itself (the rest are matched against the text)
_OWN_PARAMS = frozenset({"time_of_day", "mood", "pattern", "palette", "lights", "view", "density"})

_WORD = re.compile(r"[A-Za-z][A-Za-z'’-]*")
_POSSESSIVE = re.compile(r"['’]s$")
_QUOTE = re.compile(r'"([^"]+)"')
_DIRECTION = re.compile(r"[(\[]([^()\[\]]{1,100})[)\]]")
_SPEAKER = re.compile(
    r"^\s*(?P<name>[^\W\d_][\w.'’\u0300-\u036f\u0900-\u0dff\u0e00-\u0eff-]*"
    r"(?: [^\W\d_][\w.'’\u0300-\u036f\u0900-\u0dff\u0e00-\u0eff-]*){0,2})\s*"
    r"(?:\([^)]*\))?\s*[:\uff1a]\s*(?P<rest>\S.*)$"
)


def _split_speaker(text: str) -> tuple[str, str] | None:
    """``Name: words`` in any script -> ``(name, words)``.

    A name is one to three words of letters and combining marks (a Devanagari name such as ``मिया`` has vowel signs that
    are not letters, so a plain ``\\w`` would cut it short).  Where a script has capitals the name must be capitalised
    (``Mia``, ``Mr. Pip``: a lowercase "note:" is not a speaker); a script without case (Devanagari, Arabic, CJK, Thai ...)
    has nothing to check, so a short label before a colon is enough.
    """
    m = _SPEAKER.match(text)
    if m is None:
        return None
    name = m.group("name").strip()
    for tok in name.split():
        letters = [ch for ch in tok if unicodedata.category(ch)[0] == "L"]
        if not letters or any(
            unicodedata.category(ch)[0] not in "LM" and ch not in ".'’-" for ch in tok
        ):
            return None
        cased = [ch for ch in letters if ch.isupper() or ch.islower()]
        if cased and not tok[0].isupper():
            return None
    return name, m.group("rest")


# end of a sentence: whitespace after . ! ? ... (or the Devanagari danda) before a capital / digit /
# non-ASCII letter, and straight after the CJK full stops, which are not followed by spaces
_SPLIT = re.compile(
    r"(?<=[.!?…\u0964])\s+(?=[A-Z0-9\u0001(\[]|[^\x00-\x7f])|(?<=[\u3002\uff01\uff1f])\s*"
)
_BULLET = re.compile(r"^\s*(?:[-*•]+|\d{1,2}[.)])\s+")
_CLAUSE = re.compile(r"\s*(?:[,;:—]|\s-\s|\s--\s)\s*")

#: natural pace of a scene: lead-in before the first caption, tail after the last one
LEAD_SEC, TAIL_SEC, GAP_SEC = 0.4, 0.6, 0.15
BRIDGE_SEC = 4.8  # a silent bridge scene (typical)
MAX_BRIDGE_SEC = 7.2  # ... and the longest the planner makes one
OVERLAP_SEC = 0.45  # average transition overlap per scene boundary (for planning only)
SQUEEZE_LIMIT = 0.66  # condense when scenes would have to shrink more than this
CONDENSED_UNIT_SEC = (
    4.3  # screen time of one sentence in a condensed script, with its share of gaps
)
CONDENSED_WORDS = 14  # longest caption in a condensed script


# =============================================================================== small helpers
def _words(text: str) -> list[str]:
    """Lowercase word tokens with possessives stripped (``Mia's`` -> ``mia``)."""
    out = []
    for w in _WORD.findall(text):
        out.append(_POSSESSIVE.sub("", w.lower().replace("’", "'")))
    return out


_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]")


def _n_words(text: str) -> int:
    """Words for pacing: whitespace-separated tokens, with two CJK characters counting as one."""
    cjk = len(_CJK.findall(text))
    if not cjk:
        return len(text.split())
    return len(_CJK.sub(" ", text).split()) + math.ceil(cjk / 2)


def _titlecase(text: str) -> str:
    small = {"a", "an", "the", "of", "and", "or", "to", "in", "on", "at", "for", "with"}
    return " ".join(
        w if (i > 0 and w.lower() in small) else w[:1].upper() + w[1:]
        for i, w in enumerate(text.split())
    )


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "char"


# =============================================================================== parsing
@dataclass
class Sent:
    """One sentence of the script before casting."""

    kind: str  # narration | dialogue
    text: str  # caption text (the quote, for dialogue); "" = a silent stage direction
    attrib: str = ""  # narration around a quote ("she whispers"): speaker and cues come from it
    speaker_name: str | None = None  # from a "Name:" prefix
    directions: list[str] = field(default_factory=list)
    line: int = 0  # which line of the script it came from


@dataclass
class Parsed:
    title: str | None
    sents: list[Sent]


def _protect(text: str) -> str:
    for a in _ABBREV:
        text = text.replace(a, a.replace(".", "\u0002"))
    return text


def _sentences(text: str) -> list[str]:
    return [p.replace("\u0002", ".").strip() for p in _SPLIT.split(_protect(text)) if p.strip()]


def _is_attribution_next(after: str) -> bool:
    """Does the text after a quote continue it ("she whispers", "Ana said") rather than start a
    new sentence?"""
    nxt = after.lstrip()
    if not nxt:
        return False
    if nxt[0].islower():
        return True
    first_words = [w.lower() for w in _WORD.findall(nxt)[:4]]
    return any(w in _SPEECH_VERBS for w in first_words)


def _split_text(text: str) -> list[Sent]:
    """Split a narration paragraph into sentences; quoted speech becomes dialogue sentences."""
    text = clean_text(text.replace("“", '"').replace("”", '"'))
    quotes: list[str] = []

    def stash(m: re.Match[str]) -> str:
        q = m.group(1).strip()
        quotes.append(q)
        end = f"\u0001{len(quotes) - 1}\u0001"
        if q.endswith((".", "!", "?", "…")) and not _is_attribution_next(m.string[m.end() :]):
            end += "."  # the quote closes a sentence of its own
        return end

    text = _QUOTE.sub(stash, text).replace('"', "")
    out: list[Sent] = []
    for piece in _sentences(text):
        found = re.findall(r"\u0001(\d+)\u0001", piece)
        if not found:
            if re.search(r"\w", piece):
                out.append(Sent("narration", piece))
            continue
        rest = clean_text(re.sub(r"\u0001\d+\u0001", " ", piece)).strip(" ,;:-.")
        for idx in found:
            q = quotes[int(idx)]
            if not re.search(r"\w", q):
                continue
            parts = _sentences(_protect(q)) if _n_words(q) > 16 else [q]
            for part in parts:
                part = part.rstrip()
                if part.endswith((",", ";", ":")):  # the speech goes on after the attribution
                    part = part[:-1] + "..."
                out.append(Sent("dialogue", part, attrib=rest))
    return out


def _looks_like_title(line: str, next_blank: bool) -> bool:
    words = line.split()
    if not (1 <= len(words) <= 8) or line.endswith(("!", ".")):
        return False
    if line.endswith("?"):
        return next_blank
    caps = sum(1 for w in words if w[:1].isupper())
    return next_blank or caps >= max(1, math.ceil(0.6 * len(words)))


def parse_script(script: str) -> Parsed:
    """Title, then every sentence as narration or dialogue (quotes, ``Name:`` lines, directions)."""
    lines = [ln.rstrip() for ln in script.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    title: str | None = None
    first = next((i for i, ln in enumerate(lines) if ln.strip()), None)
    if first is not None:
        line = lines[first].strip()
        marked = re.match(r"^(?:#+\s*|title\s*:\s*)(.+)$", line, re.I)
        following = lines[first + 1 :]
        if marked:
            title = clean_text(marked.group(1)).strip("# ")
            lines[first] = ""
        elif any(ln.strip() for ln in following):
            next_blank = not following[0].strip() if following else True
            if _looks_like_title(line, next_blank):
                title = clean_text(line)
                lines[first] = ""
    sents: list[Sent] = []
    for line_no, line in enumerate(lines):
        text = _BULLET.sub("", line.strip())
        if not text or re.fullmatch(r"[-=_*#~\s]{3,}", text):
            continue
        speaker: str | None = None
        found = _split_speaker(text)
        if found:
            name, rest = found
            toks = name.split()
            ok = (
                len(toks) <= 3
                and not any(t.lower().strip(".") in _NOT_SPEAKER for t in toks)
                and not any(any(c.isdigit() for c in t) for t in toks)
            )
            if ok:
                text = rest
                key = name.lower().replace(".", "").strip()
                if key not in _NARRATOR_NAMES and key != "vo":
                    speaker = _titlecase(name.title() if name.isupper() else name)
                else:
                    speaker = ""  # a voice-over line: narration, not a character
        dirs = [clean_text(d) for d in _DIRECTION.findall(text)]
        text = clean_text(_DIRECTION.sub(" ", text))
        if speaker is None:
            pieces = _split_text(text) if text else []
        else:
            pieces = [Sent("narration", p) for p in _sentences(text.strip('"'))]
            if speaker:
                for p in pieces:
                    p.kind, p.speaker_name = "dialogue", speaker
        if pieces:
            pieces[0].directions = dirs
        elif dirs:
            pieces = [Sent("narration", "", directions=dirs)]
        for p in pieces:
            p.line = line_no
        sents.extend(pieces)
    return Parsed(title, sents)


# =============================================================================== casting
def _find_names(sents: list[Sent]) -> Counter[str]:
    """Capitalised words that behave like names: never written in lowercase, and either capitalised
    mid-sentence or repeatedly sentence-initial in front of a verb."""
    mid: Counter[str] = Counter()
    initial: Counter[str] = Counter()
    before_verb: Counter[str] = Counter()
    lower_forms: set[str] = set()
    for s in sents:
        for chunk in (s.text, s.attrib):
            toks = _WORD.findall(chunk)
            for i, tok in enumerate(toks):
                base = _POSSESSIVE.sub("", tok)
                if base.islower():
                    lower_forms.add(base)
                    continue
                if not base[:1].isupper() or len(base) < 2 or base.isupper():
                    continue  # lowercase, a lone capital, or SHOUTING / an acronym
                if i == 0:
                    initial[base] += 1
                    nxt = toks[i + 1].lower() if i + 1 < len(toks) else ""
                    if nxt.endswith(("s", "ed", "ing")) or nxt in _VERB_AFTER_NAME:
                        before_verb[base] += 1
                elif toks[i - 1].lower() not in _PLACE_PREPOSITIONS:  # "in Paris" is a place
                    mid[base] += 1
    names: Counter[str] = Counter()
    for w, n in mid.items():
        if w.lower() not in _STOP and w.lower() not in lower_forms:
            names[w] += n + initial.get(w, 0)
    for w, n in initial.items():
        if (
            w not in names
            and w.lower() not in _STOP
            and w.lower() not in lower_forms
            and n >= 2
            and before_verb[w] >= 1
        ):
            names[w] += n
    return names


#: capitalised only because they open a sentence: a time, a season, a meal, the weather ("Night fell. She ...") is nobody
_OPENING_NOUNS = frozenset(
    _w(
        """
        night morning evening afternoon noon midnight dawn dusk day days week month year time life
        winter summer spring autumn rain snow wind fog mist storm thunder lightning darkness light
        silence music dinner breakfast lunch school class work home house everyone nothing
        """
    )
)


def _first_subject(sents: list[Sent], known: set[str]) -> str | None:
    """The heroine of "Sita carried a bucket. She lowered it ...": a word that opens the first sentence in front of a verb is a
    name even when it is written only once, if the story then goes on with he or she (a plural, a thing or a time is never
    "she")."""
    if not sents or sents[0].kind != "narration":
        return None
    toks = _WORD.findall(sents[0].text)
    if len(toks) < 2:
        return None
    word = _POSSESSIVE.sub("", toks[0])
    low, nxt = word.lower(), toks[1].lower()
    if not word[:1].isupper() or len(word) < 2 or word.isupper():
        return None
    if low in _STOP or low in _OPENING_NOUNS or low in _SKY_WORDS or low in known:
        return None
    if low.endswith("s") and low[:-1] in known:  # "Birds sang"
        return None
    if not (nxt.endswith(("s", "ed", "ing")) or nxt in _VERB_AFTER_NAME):
        return None
    if any(w == low for s in sents for w in _WORD.findall(s.text) if w.islower()):
        return None  # written in lowercase somewhere: a word, not a name
    if not any(re.search(r"\b(?:he|she)\b", s.text, re.I) for s in sents[1:]):
        return None
    return word


#: what can follow a name at the start of a sentence besides a regular verb ("Kabir threw", "Kabir and his sister"): a name, not "Then" or "Today"
_VERB_AFTER_NAME = frozenset(
    _w(
        """
        is was has had did does and or went ran saw took gave came found made got felt knew told said
        sat stood held kept left met paid sent spent won wore wrote began drank ate fell flew grew hid
        heard lost put read rode sang slept swam taught thought woke threw caught brought bought built
        chose dug drew drove forgot hit hung lay led let lit meant rang rose shook shot shut slid spoke
        stole stuck swept swung tore understood waved
        """
    )
)
#: titles that sit between an adjective and a name ("Old Mr. Pip"): never descriptive themselves
_HONORIFIC = r"(?i:(?:mr|mrs|ms|miss|dr|sir|prof|professor)\.?\s+)"
#: what a character says about themselves gives them away: (pattern, a word the archetype tables know)
_SPEECH_MARKERS: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"\b(?:me|i|my)\b[^.?!]{0,12}\b(?:study|studying|homework|school|class|exam|recess|"
            r"teacher|mom|mommy|dad|daddy)\b",
            re.I,
        ),
        "student",
    ),
    (
        re.compile(
            r"\b(?:back in my day|in my day|my grandkids|my grandchildren|when i was young|my knees|"
            r"i(?:'m| am) retired)\b",
            re.I,
        ),
        "elderly",
    ),
    (
        re.compile(
            r"\b(?:my team|my employees|my staff|my office|quarterly|deadline|"
            r"you(?:'re| are) fired|report to me)\b",
            re.I,
        ),
        "manager",
    ),
    (
        re.compile(
            r"\b(?:beep|boop|my circuits|my battery|affirmative|does not compute|my sensors|"
            r"i am programmed)\b",
            re.I,
        ),
        "robot",
    ),
    (re.compile(r"\b(?:fear not|to the rescue|i will save|justice)\b", re.I), "hero"),
    (re.compile(r"बीप|बूप"), "robot"),
)


def _describe(name: str, sents: list[Sent]) -> list[str]:
    """Words that describe ``name``: itself, the words just before it ("little Mia", "Old Mr. Pip"),
    an appositive ("Pixel, the little robot," / "Mia, a curious kid with a yellow backpack,"), a
    copula phrase ("Pixel is a robot") and, for ``Name:`` dialogue, what the character says about
    themselves ("help me study")."""
    out = [name.lower()]
    n = re.escape(name)
    before = re.compile(rf"\b(?:(\w+)\s+)?(?:(\w+)\s+)?{_HONORIFIC}?{n}\b")
    apposition = re.compile(
        rf"\b{n}\s*,\s*(?:a|an|the|our|my|his|her|their)\s+([\w' -]{{1,70}}?)\s*[,.;!]"
    )
    copula = re.compile(
        rf"\b{n}\s+(?:is|was|looks|seems|became)\s+(?:(?:a|an|the)\s+)?([\w' -]{{1,70}}?)\s*[,.;!]"
    )
    titled = re.compile(rf"\b{n}\s+the\s+(\w+)")  # "Max the dog", "Pixel the robot"
    for s in sents:
        for chunk in (s.text, s.attrib):
            chunk = chunk + " ."
            for m in before.finditer(chunk):
                out += [g.lower() for g in m.groups() if g]
            for pat in (apposition, copula, titled):
                for m in pat.finditer(chunk):
                    out += m.group(1).lower().split()
        if s.speaker_name == name:  # a "Name:" line: this character is the one talking
            out += [word for pat, word in _SPEECH_MARKERS if pat.search(s.text)]
    return out


#: words that end the noun group that says what a character is ("a rickshaw driver WITH a parrot")
_GROUP_END = frozenset(
    _w(
        "with who whom whose that which from in on at of and but or while carrying holding wearing near by as"
    )
)


def _what_is(name: str, sents: list[Sent]) -> list[str]:
    """The words of the noun groups that say what ``name`` *is*: "Max the dog", "Pixel, the little robot,", "Pixel is a
    robot that ...", "her dragon Ember", "Mia's dog Max".  Unlike :func:`_describe` it never reads a word that merely
    stands near the name ("The cat scratched Mia"), so a creature next to a person does not make the person one."""
    n = re.escape(name)
    group = r"([\w' -]{1,70}?)"
    patterns = (
        re.compile(rf"\b{n}\s+the\s+(\w+)"),
        re.compile(rf"\b{n}\s*,\s*(?:a|an|the|our|my|his|her|their)\s+{group}\s*[,.;!]"),
        re.compile(
            rf"\b{n}\s+(?:is|was|looks|seems|became)\s+(?:(?:a|an|the)\s+)?{group}\s*[,.;!]"
        ),
        re.compile(rf"\b(?:a|an|the|my|your|his|her|their|our)\s+(\w+)\s+{n}\b"),
        re.compile(rf"\b\w+['’]s\s+(\w+)\s+{n}\b"),
    )
    out: list[str] = []
    for s in sents:
        for chunk in (s.text, s.attrib):
            for pat in patterns:
                for m in pat.finditer(chunk + " ."):
                    for word in m.group(1).lower().split():
                        if word in _GROUP_END:
                            break
                        out.append(word)
    return out


def _gender(words: list[str]) -> str:
    """He or she from the names and kin words around a character; a pronoun in front of the name ("His friend Anita") belongs to someone else."""
    for w in words:
        if w in _PRONOUNS:
            continue
        if w in _FEMALE:
            return "f"
        if w in _MALE:
            return "m"
    return ""


#: honorifics and titles that stand in front of a name
_HON = r"(?:Mr|Mrs|Ms|Miss|Dr|Sir|Prof)\.?"
#: words that say a character is a child (or a student) wherever they stand in the sentence that names them
_KID_CONTEXT = frozenset(
    _w(
        "school homework classroom class classmate classmates playground recess kindergarten toys toy "
        "crayons notebook pencil lunchbox exam bedtime"
    )
)
#: what Hindi and Hinglish call somebody who is old, small, a robot, a boss or a hero, as the English word the archetype tables know
_INDIC_CUES: dict[str, tuple[str, ...]] = {
    "elderly": ("बूढ़ा", "बूढ़े", "बूढ़ी", "बुज़ुर्ग", "बुजुर्ग", "दादा", "दादी", "नाना", "नानी",
                "boodha", "budha", "boorha", "buzurg", "dada", "dadi", "nana", "nani"),
    "robot": ("रोबोट",),
    "kid": ("बच्चा", "बच्ची", "बच्चे", "लड़का", "लड़की", "बालक", "बालिका",
            "baccha", "bachcha", "bachchi", "bacchi", "ladka", "ladki", "balak", "balika"),
    "little": ("छोटा", "छोटी", "नन्हा", "नन्ही", "chhota", "chhoti", "nanha", "nanhi"),
    "boss": ("मालिक", "साहब", "मैनेजर", "बॉस", "malik", "sahab", "manager"),
    "hero": ("हीरो", "नायक", "योद्धा", "वीर"),
}  # fmt: skip
_INDIC_CUE_OF = {w: english for english, ws in _INDIC_CUES.items() for w in ws}
#: the plain man the engine draws when a script says nobody in particular (the library has no "man" to add: this is it)
_EVERYMAN_WORDS = (
    "man",
    "men",
    "person",
    "guy",
    "stranger",
    "आदमी",
    "आदमियों",
    "इंसान",
    "aadmi",
    "insan",
)
#: words that say a script is Hindi written in Latin letters ("Rahul ne apni copy kholi"): its nouns come without "the" and "a".
#: (Not "the" or "par": they are English words, and "the" alone is one word in eight of an English script.)
_HINGLISH_WORDS = frozenset(
    _w(
        """
        hai hain tha thi ka ki ke ko se mein aur nahi nahin kya ek ne wala wali raha rahi gaya gayi gaye kiya kar
        liye bhi toh yeh woh unke uske apne apni apna mera meri tera teri hum tum aap kuch bahut
        """
    )
)
#: capitalised words that are places and things in the sky, not somebody
_SKY_WORDS = frozenset(
    _w(
        "earth moon sun mars venus jupiter saturn mercury neptune uranus pluto universe galaxy world"
    )
)
#: a creature or role that comes into the story as the subject of a clause ("An elephant arrived", "... and the tiger watched"):
#: a guest who is on screen in that scene, not someone a prepositional phrase merely mentions ("as brave as a lion")
_GUEST_RE = re.compile(
    r"(?:^|[.!?]\s+|[,;]\s+|\b(?:and|but|then|when|while|suddenly|soon|until|before|after|so)\s+)"
    r"(?:the|a|an)\s+([\w'’-]+)(?=((?:\s+[\w'’-]+){0,2}))",
    re.I,
)
MAX_CAST = MAX_ON_SCREEN + 2  # characters in a reel; no more than MAX_ON_SCREEN are in one scene


def _role_words(name: str, sents: list[Sent], known: set[str]) -> list[str]:
    """The words a script uses to say what ``name`` is when the role stands beside or before the name rather than after it:
    "Chef Marco", "Dr. Meera", "a clever monkey named Chiku", "His cow, Gauri," and "Her boss, Mr. Rao,".  Only words in
    ``known`` (the roles and creatures the engine or the library can draw) are returned, in reading order."""
    n = re.escape(name)
    pats = (
        re.compile(rf"\b(\w+)\s+(?:{_HON}\s+)?{n}\b"),
        re.compile(rf"\b(\w+)\s+named\s+(?:{_HON}\s+)?{n}\b"),
        # "a dog called Max" names the dog, "The farmer called Mia" is the farmer calling her: only an indefinite or possessive noun group is read
        re.compile(
            rf"\b(?:a|an|my|your|his|her|our|their)\s+(?:\w+\s+){{0,2}}?(\w+)\s+called\s+(?:{_HON}\s+)?{n}\b"
        ),
        re.compile(rf"\b(\w+)\s*,\s*(?:{_HON}\s+)?{n}\b"),
    )
    doctor = re.compile(rf"\bDr\.?\s+{n}\b")
    out: list[str] = []
    for s in sents:
        for chunk in (s.text, s.attrib):
            found: list[tuple[int, str]] = []
            for pat in pats:
                found += [(m.start(), m.group(1).lower()) for m in pat.finditer(chunk)]
            found += [(m.start(), "doctor") for m in doctor.finditer(chunk)]
            out += [w for _pos, w in sorted(found) if w in known]
    return out


def _context_gender(name: str, others: set[str], sents: list[Sent]) -> str:
    """He or she, read from the pronouns that follow ``name`` in its sentence or start the sentence after it: "Sita went to the
    market with her bag", "Rohan opened the door. He smiled."  Sentences that name someone else as well are not read."""
    votes: Counter[str] = Counter()
    pat = re.compile(rf"\b{re.escape(name)}\b")
    others = others - {name}
    other_pats = [re.compile(rf"\b{re.escape(o)}\b") for o in others]

    def alone(chunk: str) -> bool:
        return not any(p.search(chunk) for p in other_pats)

    for i, s in enumerate(sents):
        chunk = " ".join(c for c in (s.text if s.kind == "narration" else "", s.attrib) if c)
        m = pat.search(chunk)
        if m is None or not alone(chunk):
            continue
        for w in _words(chunk[m.end() :]):
            g = _PRONOUNS.get(w)
            if g in ("m", "f"):
                votes[g] += 1
        nxt = sents[i + 1] if i + 1 < len(sents) else None
        if nxt is not None and nxt.kind == "narration" and nxt.text:
            first = (_words(nxt.text) or [""])[0]
            g = _PRONOUNS.get(first)
            if g in ("m", "f"):
                votes[g] += 1
    if votes["f"] != votes["m"]:
        return "f" if votes["f"] > votes["m"] else "m"
    return ""


def _indic_cue_words(name: str, sents: list[Sent]) -> list[str]:
    """What the words round ``name`` say it is when the script is in Hindi (or Hinglish): "बूढ़े पिप जी" is old, "छोटा रोबोट"
    called Bolt is a robot.  The words within three places of the name, as the English words the archetype tables know."""
    from reel.assets.match import tokens

    want = [t for t, _s, _e in tokens(name)]
    if not want:
        return []
    out: list[str] = []
    for s in sents:
        for chunk in (s.text, s.attrib):
            toks = [t for t, _s, _e in tokens(chunk)]
            for i in range(len(toks) - len(want) + 1):
                if toks[i : i + len(want)] != want:
                    continue
                near = toks[max(0, i - 3) : i] + toks[i + len(want) : i + len(want) + 3]
                out += [_INDIC_CUE_OF[w] for w in near if w in _INDIC_CUE_OF]
    return out


def _is_hinglish(sents: list[Sent]) -> bool:
    """Is the script Hindi written in Latin letters?  A few of its small words are enough to tell from English."""
    toks = [w for s in sents for w in _words(" ".join([s.text, s.attrib]))]
    hits = [w for w in toks if w in _HINGLISH_WORDS]
    # one small word said again and again is not a language (three different ones, and a share of the script, are)
    return len(hits) >= 4 and len(set(hits)) >= 3 and len(hits) / max(1, len(toks)) >= 0.12


def _kid_context(name: str, sents: list[Sent]) -> bool:
    """Is ``name`` met in a sentence about school, toys or bedtime: somebody young?"""
    pat = re.compile(rf"\b{re.escape(name)}\b")
    for s in sents:
        for chunk in (s.text, s.attrib):
            if pat.search(chunk) and _KID_CONTEXT & set(_words(chunk)):
                return True
    return False


def build_cast(
    parsed: Parsed,
    available_archetypes: set[str],
    seed: int,
    library: LibraryPlanner | None = None,
) -> Cast:
    """Up to three characters from names, ``Name:`` prefixes and role nouns (and the guests a scene brings in); else one narrator.

    ``library`` adds the characters of the asset library: a creature or a role it can draw ("the dog", "a farmer") is a role
    noun like the engine's own, and a named character described as one ("Max the dog", "Chef Marco", "Her boss, Mr. Rao,") is
    drawn as it, and answers to the noun as well as to the name.  A script in another language has no "the dog" for the rules to
    read: its words ("कुत्ता") are matched to the library's tags directly, and a character it names is cast as the asset that
    answers to the word."""
    sents = parsed.sents
    lib_words = library.character_words() if library else {}
    known = set(_ROLE_NOUNS) | set(lib_words)
    names = _find_names(sents)
    lone = _first_subject(sents, known)
    if lone is not None and lone not in names:
        names[lone] = 1
    speakers = {s.speaker_name for s in sents if s.speaker_name}
    for who, said in Counter(s.speaker_name for s in sents if s.speaker_name).items():
        names[who] += 100 + said
    # what each name is, when the script says so: "Chef Marco", "a monkey named Chiku", "Max the dog", "Her boss, Mr. Rao,"
    role_of: dict[str, list[str]] = {}
    for n in names:
        what_n = [w for w in _what_is(n, sents) + _role_words(n, sents, known) if w in known]
        if what_n:
            role_of[n] = what_n
    # a capitalised thing in the sky ("Earth") or one the library draws ("Moon") is not somebody, unless the script says what it is
    thing_words = _SKY_WORDS | (set(library.objects.single_words()) if library else set())
    for n in list(names):
        low = n.lower()
        if (
            low in thing_words
            and low not in _FEMALE
            and low not in _MALE
            and n not in role_of
            and n not in speakers
        ):
            del names[n]
    # the role nouns of named characters are their other names ("the boss" is Mr. Rao): they cast nobody else
    aliases: dict[str, list[str]] = {}
    for n, words in role_of.items():
        if n in names:
            aliases.setdefault(words[0], []).append(n)
    roles: Counter[str] = Counter()
    lowered = {n.lower() for n in names}
    hinglish = _is_hinglish(sents)
    for s in sents:
        for chunk in (s.text, s.attrib):
            nouns = _role_nouns(chunk, known)
            if hinglish:  # "teacher ne kaha": the English noun comes without "the"
                nouns = [w for w in _words(chunk) if w in lib_words and w.isascii()]
            for noun in nouns:
                if (
                    (noun in _ROLE_NOUNS or noun in lib_words)
                    and noun not in lowered
                    and noun not in aliases
                ):
                    roles[noun] += 1
    foreign: Counter[str] = Counter()  # library characters named in another language, by asset name
    foreign_first: dict[str, int] = {}
    if library:
        for i, s in enumerate(sents):
            for chunk in (s.text, s.attrib):
                for h in library.foreign_character_hits(chunk):
                    foreign[h.asset.name] += 1
                    foreign_first.setdefault(h.asset.name, i)

    def first_index(label: str) -> int:
        pat = re.compile(rf"\b{re.escape(label)}\b")
        for i, s in enumerate(sents):
            if pat.search(s.text) or pat.search(s.attrib) or s.speaker_name == label:
                return i
        return len(sents)

    ranked = sorted(names, key=lambda n: (-names[n], first_index(n), n))
    chosen: list[tuple[str, str]] = [(n, "name") for n in ranked]
    named = len(chosen)
    for noun, times in sorted(roles.items(), key=lambda kv: (-kv[1], kv[0])):
        if times >= 2 or named == 0:  # a role noun counts when it recurs, or when nothing else does
            chosen.append((noun, "role"))
    cast_by_word = {
        lib_words.get(n, n) for n, how in chosen if how == "role"
    }  # what an English noun already casts
    for asset, times in sorted(
        foreign.items(), key=lambda kv: (-kv[1], foreign_first[kv[0]], kv[0])
    ):
        if asset in cast_by_word:
            continue  # "the dog" and "कुत्ता" in one script are one dog
        if times >= 2 or named == 0:  # the same rule for the words of another language
            chosen.append((asset, "library"))
    if not chosen:  # nobody to point at: a narrator speaks every line on screen
        arch = guess_archetype([], available_archetypes)
        return Cast([Char("narrator", "Narrator", arch, count=1)], explainer=True)
    chars: list[Char] = []
    used: set[str] = set()
    cast_as: dict[str, Char] = {}  # library asset name -> the character drawn as it
    main = chosen[:MAX_ON_SCREEN]
    for label, how in main:
        shown = label.replace("_", " ")
        words = (
            _describe(label, sents) + _indic_cue_words(label, sents) if how == "name" else [shown]
        )
        cid = _slug(label)
        base, k = cid, 2
        while cid in used:
            cid = f"{base}_{k}"
            k += 1
        used.add(cid)
        noun = ""
        gender = _gender(words)
        if how == "library":
            arch, first, count = label, foreign_first[label], foreign[label]
        else:
            what = role_of.get(label, []) if how == "name" else words
            arch = guess_archetype(words, available_archetypes)
            lib_role = next((w for w in what if w in lib_words), None)
            if (
                lib_role is not None
            ):  # a creature or role the library draws beats a body picked from stray words ("little dog Max")
                arch = lib_words[lib_role]
            elif arch == "everyman":
                engine = next(
                    (_ROLE_NOUNS[w] for w in what if _ROLE_NOUNS.get(w) in available_archetypes),
                    None,
                )
                arch = engine or arch
            if how == "name":
                noun = next((w for w in what if w in known), "")
                gender = gender or _context_gender(label, set(names), sents)
            first, count = first_index(label), names.get(label, roles.get(label, 0))
            if arch in (
                "student_boy",
                "student_girl",
            ):  # the library has one of each: a she is the girl
                girl = "student_girl" in available_archetypes
                arch = (
                    "student_girl"
                    if gender == "f" and girl
                    else "student_boy"
                    if "student_boy" in available_archetypes
                    else arch
                )
            elif arch == "everyman" and how == "name":
                if _kid_context(label, sents) and "kid" in available_archetypes:
                    arch = "kid"  # met at school or with toys: a child, whoever they are
        char = Char(
            cid, label if how == "name" else shown.capitalize(), arch, gender,
            noun if how == "name" and len(aliases.get(noun, [])) == 1 else shown if how != "name" else "", count, first=first,
        )  # fmt: skip
        chars.append(char)
        if how == "library":
            cast_as[label] = char
    # a creature or role that only comes in once ("An elephant arrived", "... and the tiger watched") is a guest in that scene
    if library and len(chars) < MAX_CAST:
        guest_first: dict[str, int] = {}
        guest_times: Counter[str] = Counter()
        for i, s in enumerate(sents):
            for chunk in (s.text if s.kind == "narration" else "", s.attrib, *s.directions):
                found = _guest_nouns(chunk, known)
                if hinglish:  # no "the" or "a" to look for: a word the library draws is enough
                    found = [w for w in _words(chunk) if w in lib_words and w.isascii()]
                for noun in found:
                    if (
                        (noun in lib_words or noun in _ROLE_NOUNS)
                        and noun not in aliases
                        and noun not in lowered
                    ):
                        guest_times[noun] += 1
                        guest_first.setdefault(noun, i)
        have = {c.archetype for c in chars}
        for noun in sorted(guest_times, key=lambda w: (guest_first[w], w)):
            # what the library draws, else the engine's own body for the role ("a little girl came in": a kid)
            arch = lib_words.get(noun) or _ROLE_NOUNS[noun]
            if arch not in available_archetypes and arch not in lib_words.values():
                continue
            if arch in have or len(chars) >= MAX_CAST or any(c.role == noun for c in chars):
                continue
            if arch in ("student_boy", "student_girl") or arch == "woman":
                continue  # people who are about, not guests: the rules above cast them when the script names them
            have.add(arch)
            cid, k = _slug(arch), 2  # drawn as the asset: "puppy" and "kutta" are both the dog
            while cid in used:
                cid = f"{_slug(arch)}_{k}"
                k += 1
            used.add(cid)
            chars.append(
                Char(
                    cid,
                    arch.replace("_", " ").capitalize(),
                    arch,
                    "",
                    noun,
                    guest_times[noun],
                    first=guest_first[noun],
                    guest=True,
                )
            )
    # ... and so is a creature a Hindi or Hinglish script names once ("एक कुत्ता", "ek chhota kutta")
    if library and len(chars) < MAX_CAST:
        have = {c.archetype for c in chars}
        for asset, times in sorted(foreign.items(), key=lambda kv: (foreign_first[kv[0]], kv[0])):
            if (
                asset in have
                or len(chars) >= MAX_CAST
                or asset in ("student_boy", "student_girl", "woman")
            ):
                continue
            have.add(asset)
            cid, k = _slug(asset), 2
            while cid in used:
                cid = f"{_slug(asset)}_{k}"
                k += 1
            used.add(cid)
            guest = Char(
                cid,
                asset.replace("_", " ").capitalize(),
                asset,
                "",
                "",
                times,
                first=foreign_first[asset],
                guest=True,
            )
            chars.append(guest)
            cast_as[asset] = guest
    chars.sort(key=lambda c: (c.first, -c.count))
    rng = random.Random(stable_int("palette", seed, *[c.id for c in chars]))
    offset, skin0 = rng.randrange(len(_PALETTES)), rng.randrange(len(_SKINS))
    for i, c in enumerate(chars):
        shirt, pants, accent = _PALETTES[(offset + i) % len(_PALETTES)]
        c.palette = {"shirt": shirt, "pants": pants, "accent": accent}
        if c.archetype != "robot":
            c.palette["skin"] = _SKINS[(skin0 + 2 * i) % len(_SKINS)]
        roles_of = library.sprite_roles(c.archetype) if library else None
        if (
            roles_of is not None
        ):  # a picture keeps its own colours: only the parts it marks can change
            c.palette = {k: v for k, v in c.palette.items() if k in roles_of}
    cast = Cast(chars, explainer=False)
    for c in chars:
        cast.by_name[c.name.lower()] = c
        cast.by_name[c.id] = c
        if c.role:
            cast.by_name[c.role] = c
    for asset_name, c in cast_as.items():  # so the words of another language find them again
        cast.by_name.setdefault(asset_name, c)
    return cast


# =============================================================================== units
@dataclass
class Unit:
    """One caption's worth of script, with who says it and what happens."""

    text: str
    kind: str  # narration | dialogue
    speaker: str | None
    mentions: list[str]
    performer: str | None
    cues: list[Cue]
    shout: bool = False
    tokens: list[str] = field(default_factory=list)
    score: float = 0.0
    silent: bool = False  # a stage direction without a caption
    plural: bool = False  # "they follow ...", "everyone looks ...": the whole cast acts
    line: int = -1  # the script line it came from
    speech: list[str] = field(
        default_factory=list
    )  # the words of a quote: a place named in speech only says where somebody is talking about
    narr_text: str = (
        ""  # what is told rather than said: narration, the words around a quote, stage directions
    )

    @property
    def words(self) -> int:
        return _n_words(self.text)

    @property
    def read_sec(self) -> float:
        """Comfortable on-screen time: ~2.6 words per second plus a beat."""
        return 2.2 if self.silent else max(1.3, self.words / READ_WPS + 0.4)


def _resolve_pronoun(word: str, cast: Cast, recent: list[str]) -> str | None:
    """The most recently active character a pronoun can refer to (unknown genders fit anything)."""
    g = _PRONOUNS.get(word)
    pool = [cid for cid in recent if cast.get(cid) is not None]
    if g is None or not pool:
        return None
    if g in ("m", "f"):
        for cid in pool:
            c = cast.get(cid)
            if c is not None and c.gender in (g, ""):
                return cid
    return pool[0]


def _library_mentions(
    text: str, cast: Cast, library: LibraryPlanner | None
) -> list[tuple[int, str]]:
    """(position, character id) of the cast's library characters that ``text`` names in another language."""
    if library is None:
        return []
    found: list[tuple[int, str]] = []
    for h in library.foreign_character_hits(text):
        c = cast.by_name.get(h.asset.name)
        if c is not None:
            found.append((h.start, c.id))
    return found


def build_units(parsed: Parsed, cast: Cast, library: LibraryPlanner | None = None) -> list[Unit]:
    """Sentences -> units with speakers, mentions and cues (pronouns resolved by recency)."""
    units: list[Unit] = []
    recent: list[str] = []  # most recently active character first
    last_speaker: str | None = None
    prev: Sent | None = None
    narrator = cast.chars[0].id if cast.explainer else None

    def named_in(text: str) -> list[tuple[int, str]]:
        return sorted([*mentions(text, cast), *_library_mentions(text, cast, library)])

    guest_ids = {
        c.id for c in cast.chars if c.guest
    }  # a guest is in its own sentence only: never "whoever it is about" later

    def bump(cid: str | None) -> None:
        if cid is not None and cid not in guest_ids:
            if cid in recent:
                recent.remove(cid)
            recent.insert(0, cid)

    for s in parsed.sents:
        # who is *seen*: names in narration, in the attribution around a quote and in stage
        # directions.  A name inside a quote ("Grandpa, come here!") is only being called.
        seen_text = " ".join([s.attrib, *s.directions, s.text if s.kind == "narration" else ""])
        ordered: list[str] = []
        for _, cid in named_in(seen_text):
            if cid not in ordered:
                ordered.append(cid)
        lead = _words(seen_text)
        pronoun = next((w for w in lead if w in _PRONOUNS and w not in PLURAL_WORDS), None)
        plural = s.kind == "narration" and not cast.explainer and bool(set(lead[:6]) & PLURAL_WORDS)
        speaker: str | None = None
        if cast.explainer:
            speaker = narrator if s.text else None
        elif s.kind == "dialogue":
            if s.speaker_name:
                c = cast.by_name.get(s.speaker_name.lower())
                speaker = c.id if c else None
            else:
                att = [cid for _, cid in named_in(s.attrib)]
                if att:
                    speaker = att[0]
                elif pronoun:
                    speaker = _resolve_pronoun(pronoun, cast, recent)
                if speaker is None:
                    pool = [*recent, *[c.id for c in cast.chars if c.id not in recent]]
                    if prev is not None and prev.kind == "dialogue" and prev.line == s.line:
                        speaker = last_speaker  # "Hi," she said. "How are you?" - same voice
                    elif prev is not None and prev.kind == "dialogue":  # next turn in a talk
                        speaker = next((c for c in pool if c != last_speaker), None)
                    else:  # a quote right after narration belongs to whoever that is about
                        speaker = pool[0] if pool else None
        performer = narrator if cast.explainer else speaker
        if performer is None:
            if ordered:
                performer = ordered[0]
            elif pronoun:
                performer = _resolve_pronoun(pronoun, cast, recent)
            if performer is None and recent:
                performer = recent[0]
        present = list(ordered)
        if speaker and speaker not in present:
            present.insert(0, speaker)
        if performer and performer not in present:
            present.append(performer)
        # cues come from the narration around an action; a quote is only mined when it is all we have
        narrative = " ".join([s.attrib, s.text if s.kind == "narration" else "", *s.directions])
        if cast.explainer or not narrative.strip():
            narrative = " ".join([narrative, s.text])
        cues = extract_cues(narrative, performer, cast)
        told = " ".join([s.attrib, *s.directions, s.text if s.kind == "narration" else ""]).strip()
        units.append(
            Unit(
                s.text, s.kind, speaker, present, performer, cues,
                tokens=_words(" ".join([s.text, s.attrib, *s.directions])),
                silent=not s.text, plural=plural,
                speech=_words(s.text) if s.kind == "dialogue" else [], narr_text=told,
            )
        )  # fmt: skip
        last_speaker = speaker or last_speaker
        prev = s
        bump(performer)
        for cid in reversed(ordered):
            bump(cid)
    for u in units:
        u.score = _salience(u)
    return units


def _salience(u: Unit) -> float:
    """How much a sentence carries when only some can stay: speech, drama, who and what happens,
    and enough words to say something (one-word reactions rank low)."""
    t = u.text
    return (
        (2.0 if u.kind == "dialogue" else 0.0)
        + (1.5 if t.endswith("!") else 0.0)
        + (1.0 if t.endswith("?") else 0.0)
        + (1.0 if u.mentions else 0.0)
        + 0.8 * len([c for c in u.cues if not c.kind.startswith("mood:")])
        + 1.5 * min(u.words, 14) / 14
        + (1.2 if _PLACE_WORDS & set(u.tokens) else 0.0)  # a change of place is worth keeping
        - (1.0 if u.words < 3 and not u.shout else 0.0)
    )


def _mark_shout(units: list[Unit]) -> None:
    """The last exclamation of at most six words becomes the reel's ``shout`` punchline."""
    for u in reversed(units):
        t = u.text.strip()
        if t.endswith("!") and 1 <= _n_words(t) <= 6:
            u.shout = True
            return


# =============================================================================== text shaping
_TITLE_TAIL = frozenset(_w("a an the of on in at to and or but with for by from as is was"))


def _derive_title(sentence: str) -> str:
    """A short headline from the first sentence: its first clause, at most six words."""
    first = re.split(r"(?<=[.!?…])\s", sentence.strip())[0].rstrip(".!?… ")
    first = re.sub(
        r"^(once upon a time|one day|it was|there was|there were)[, ]*", "", first, flags=re.I
    )
    words = (_CLAUSE.split(first)[0] or first).split()
    if len(words) > 6:
        words = words[:5]
    while words and words[-1].lower().strip(",;:") in _TITLE_TAIL:
        words.pop()
    return _titlecase(" ".join(words)) if words else "Untitled Reel"


def _clauses(text: str) -> list[str]:
    parts = [p.strip() for p in _CLAUSE.split(text) if p.strip()]
    return parts or [text.strip()]


def _condense(text: str, max_words: int) -> str:
    """Shorten to at most ``max_words``: the longest run of leading clauses that fits ("Mia, a kid
    with a backpack, skipped down the street"), or a cut with an ellipsis when one clause is too long."""
    if _n_words(text) <= max_words:
        return text
    kept: list[str] = []
    count = 0
    for part in _clauses(text):
        if count + _n_words(part) > max_words:
            break
        kept.append(part)
        count += _n_words(part)
    if count >= 3:
        joined = ", ".join(kept).rstrip(",;:")
        return joined if joined[-1:] in ".!?" else joined + "."
    return " ".join(text.split()[:max_words]).rstrip(",;:.") + "..."


def _split_unit(u: Unit) -> tuple[Unit, Unit] | None:
    """Split a long unit near its middle clause boundary; None when it cannot be split."""
    parts = _clauses(u.text)
    if len(parts) < 2 and u.words >= 10:
        w = u.text.split()
        k = len(w) // 2
        parts = [" ".join(w[:k]), " ".join(w[k:])]
    if len(parts) < 2:
        return None
    total = sum(_n_words(p) for p in parts)
    acc, cut = 0, 1
    for i, p in enumerate(parts[:-1], 1):
        acc += _n_words(p)
        cut = i
        if acc >= total / 2:
            break
    a, b = " ".join(parts[:cut]).rstrip(",;:"), " ".join(parts[cut:])
    if _n_words(a) < 2 or _n_words(b) < 2:
        return None
    first = Unit(a, u.kind, u.speaker, list(u.mentions), u.performer, list(u.cues),
                 tokens=list(u.tokens), score=u.score)  # fmt: skip
    second = Unit(b, u.kind, u.speaker, list(u.mentions), u.performer, [],
                  shout=u.shout, tokens=list(u.tokens), score=u.score)  # fmt: skip
    return first, second


# =============================================================================== catalog access
def _enum_hit(value: str, text: set[str]) -> bool:
    """Does an enum value show up in the scene text ("kitchen"; "rain" ~ "raining"; "ideas" ~ "idea")?"""
    v = value.lower()
    if len(v) < 3 or v in {"auto", "day", "sky", "true", "false", "none", "normal", "clear"}:
        return False
    return any(
        w == v or (len(v) >= 4 and w.startswith(v)) or v in (w + "s", w + "es") for w in text
    )


# =============================================================================== scene planning
@dataclass
class SceneIn:
    """A planned scene before it is rendered to JSON."""

    units: list[Unit]
    kind: str = "content"  # content | bridge
    seconds: float = 0.0  # natural length of a bridge scene

    @property
    def shout_index(self) -> int | None:
        return next((i for i, u in enumerate(self.units) if u.shout), None)

    @property
    def nat(self) -> float:
        return self.seconds if self.kind == "bridge" else _nat_duration(self.units)


def _nat_duration(units: list[Unit]) -> float:
    if not units:
        return BRIDGE_SEC
    return LEAD_SEC + sum(u.read_sec for u in units) + GAP_SEC * (len(units) - 1) + TAIL_SEC


def _partition(weights: list[float], k: int) -> list[int]:
    """Start index of each of ``k`` contiguous groups with balanced sums (least squares)."""
    n = len(weights)
    k = max(1, min(k, n))
    pre = [0.0]
    for w in weights:
        pre.append(pre[-1] + w)
    inf = float("inf")
    dp = [[inf] * (n + 1) for _ in range(k + 1)]
    cut = [[0] * (n + 1) for _ in range(k + 1)]
    dp[0][0] = 0.0
    for g in range(1, k + 1):
        for i in range(g, n + 1):
            for j in range(g - 1, i):
                c = dp[g - 1][j] + (pre[i] - pre[j]) ** 2
                if c < dp[g][i]:
                    dp[g][i], cut[g][i] = c, j
    starts: list[int] = []
    i = n
    for g in range(k, 0, -1):
        i = cut[g][i]
        starts.append(i)
    return sorted(starts)


def _group_units(units: list[Unit], n_groups: int) -> list[list[Unit]]:
    """Split into ``n_groups`` contiguous, evenly long scenes; the shout always stands alone."""
    shout_at = next((i for i, u in enumerate(units) if u.shout), None)
    if shout_at is None:
        segments = [units]
    else:
        segments = [s for s in (units[:shout_at], [units[shout_at]], units[shout_at + 1 :]) if s]
    weights = [sum(u.read_sec for u in seg) for seg in segments]
    alloc = [1] * len(segments)
    for _ in range(max(0, n_groups - len(segments))):
        j = max(
            range(len(segments)),
            key=lambda i: weights[i] / alloc[i] if alloc[i] < len(segments[i]) else -1.0,
        )
        if alloc[j] >= len(segments[j]):
            break
        alloc[j] += 1
    groups: list[list[Unit]] = []
    for seg, k in zip(segments, alloc):
        bounds = [*_partition([u.read_sec + GAP_SEC for u in seg], k), len(seg)]
        groups.extend(seg[a:b] for a, b in itertools.pairwise(bounds) if b > a)
    return groups


def _select_units(units: list[Unit], k: int) -> list[Unit]:
    """Keep ``k`` units spread over the whole script: first, last, the shout, then the most salient
    sentence of each stretch in between."""
    if len(units) <= k:
        return units
    keep = {0, len(units) - 1} | {i for i, u in enumerate(units) if u.shout}
    middle = [i for i in range(len(units)) if i not in keep]
    slots = max(0, k - len(keep))
    if slots and middle:
        size = len(middle) / slots
        for s in range(slots):
            seg = middle[int(s * size) : max(int(s * size) + 1, int((s + 1) * size))]
            if seg:
                keep.add(max(seg, key=lambda i: (units[i].score, -i)))
    return [units[i] for i in sorted(keep)]


def _total(scenes: list[SceneIn]) -> float:
    return sum(s.nat for s in scenes) - OVERLAP_SEC * max(0, len(scenes) - 1)


def _estimated_total(units: list[Unit]) -> float:
    """Natural length of ``units`` told in ten scenes: reading time, gaps, lead-ins and tails."""
    return sum(u.read_sec + GAP_SEC for u in units) + 10 * (LEAD_SEC + TAIL_SEC) - OVERLAP_SEC * 9


def _merge_runs(units: list[Unit]) -> list[Unit]:
    """Join the neighbouring pieces of one speech ("Don't worry! I will help.") back into a single
    caption while it stays short, so choosing sentences never keeps half a line."""
    out: list[Unit] = []
    for u in units:
        prev = out[-1] if out else None
        if (
            prev is not None
            and u.kind == prev.kind == "dialogue"
            and u.line == prev.line
            and u.speaker == prev.speaker
            and not (u.shout or prev.shout or u.silent or prev.silent)
            and prev.words + u.words <= CONDENSED_WORDS
        ):
            out[-1] = replace(
                prev,
                text=f"{prev.text} {u.text}",
                cues=prev.cues + u.cues,
                tokens=prev.tokens + u.tokens,
                score=max(prev.score, u.score),
            )
        else:
            out.append(u)
    return out


def _condense_units(units: list[Unit], goal: float) -> list[Unit]:
    """A script far too long for the reel: keep the sentences that carry it (the first, the last,
    the punchline and the most telling one of every stretch in between), each cut to a clause or a
    line - as many as it takes to fill the reel, so no gaps are left to pad."""
    merged = _merge_runs(units)
    kept = merged
    for k in range(MIN_SCENES, len(merged) + 1):
        kept = [
            u if u.shout else replace(u, text=_condense(u.text, CONDENSED_WORDS))
            for u in _select_units(merged, k)
        ]
        if _estimated_total(kept) >= goal * 1.05:
            break
    return kept


def plan_scenes(units: list[Unit], goal: float) -> list[SceneIn]:
    """Group units into 8-14 scenes whose natural lengths add up to roughly ``goal`` seconds:
    an over-long script is cut down to its most telling sentences, long sentences are split when
    the script is short, and silent bridge scenes pad what is still missing."""
    work = list(units)
    if _estimated_total(work) > goal / SQUEEZE_LIMIT:
        work = _condense_units(work, goal)
    for _ in range(40):  # not enough beats for 8 scenes: split long sentences at their clauses
        if len(work) >= MIN_SCENES:
            break
        candidates = sorted(
            (i for i, u in enumerate(work) if u.words >= 8 and not u.silent),
            key=lambda i: -work[i].words,
        )
        for i in candidates:
            pair = _split_unit(work[i])
            if pair:
                work[i : i + 1] = list(pair)
                break
        else:
            break
    if len(work) >= MIN_SCENES:
        best: tuple[float, list[list[Unit]]] | None = None
        for n in range(MIN_SCENES, min(MAX_SCENES, len(work)) + 1):
            groups = _group_units(work, n)
            durs = [_nat_duration(g) for g in groups]
            total = sum(durs) - OVERLAP_SEC * (len(groups) - 1)
            cost = (
                abs(total - goal) + 3.0 * max(0.0, max(durs) - 7.2) + 0.03 * abs(len(groups) - 10)
            )
            if best is None or cost < best[0]:
                best = (cost, groups)
        assert best is not None
        scenes = [SceneIn(g) for g in best[1]]
    else:
        scenes = [SceneIn([u]) for u in work]
    deficit = goal - _total(scenes)
    need = max(MIN_SCENES - len(scenes), 0)
    if deficit > 5.0:
        need = max(need, math.ceil((deficit - 2.0) / (MAX_BRIDGE_SEC - OVERLAP_SEC)))
    need = min(need, MAX_SCENES - len(scenes))
    if need <= 0:
        return scenes
    content = sum(s.nat for s in scenes)
    each = (goal + OVERLAP_SEC * (len(scenes) + need - 1) - content) / need
    return _insert_bridges(scenes, need, min(MAX_BRIDGE_SEC, max(BRIDGE_SEC - 0.8, each)))


def _insert_bridges(scenes: list[SceneIn], count: int, seconds: float) -> list[SceneIn]:
    """Spread ``count`` silent bridge scenes evenly through the story, never before the first scene
    and (when there is a choice) never after the last, so the ending stays the ending."""
    if count <= 0:
        return scenes
    n = len(scenes)
    gaps = n - 1 if n > 1 else 1  # gap g sits after scene g
    base, rem = divmod(count, gaps)
    quota = [base] * gaps
    for j in range(rem):
        quota[min(gaps - 1, int((j + 0.5) * gaps / rem))] += 1
    out: list[SceneIn] = []
    for i, s in enumerate(scenes):
        out.append(s)
        if i < gaps:
            out.extend(SceneIn([], kind="bridge", seconds=seconds) for _ in range(quota[i]))
    return out


# =============================================================================== the generator
class HeuristicSpecGenerator(SpecGenerator):
    """Rule-based, deterministic and offline: ``(script, style, seed, target_duration) -> spec``.

    ``audio`` is the block written into the spec (default: procedural music + TTS voice-over;
    ``{}`` gives the schema defaults, i.e. silent).
    """

    label = "heuristic"

    def __init__(self, catalog: Catalog | None = None, audio: dict[str, Any] | None = None) -> None:
        self.catalog = catalog
        self.audio: dict[str, Any] = default_audio() if audio is None else dict(audio)

    def generate(
        self,
        script: str,
        *,
        style: str,
        seed: int = 0,
        target_duration: float = 50.0,
    ) -> GenerationResult:
        cat = self.catalog or CATALOG
        if not isinstance(script, str) or not script.strip():
            raise SpecGenerationError("the script is empty; write at least a few sentences")
        if style not in cat.styles:
            close = cat.styles.suggest(style)
            raise SpecGenerationError(
                f"unknown style {style!r}; registered styles: {', '.join(cat.styles.names())}"
                + (f" (did you mean {close[0]!r}?)" if close else "")
            )
        goal = clamp_target(target_duration)
        notes: list[str] = []
        if abs(goal - float(target_duration)) > 1e-9:
            notes.append(
                f"target duration {target_duration:g}s clamped to {goal:g}s (budget 45-60s)"
            )
        parsed = parse_script(script)
        if not parsed.sents:
            raise SpecGenerationError("the script has no sentences to turn into scenes")
        library = LibraryPlanner(cat)
        cast = build_cast(parsed, set(cat.archetypes.names()), seed, library)
        units = build_units(parsed, cast, library)
        _mark_shout(units)
        rng = random.Random(stable_int("heuristic", seed, style, clean_text(script)))
        scenes = plan_scenes(units, goal)
        builder = _Builder(CatalogView(cat), cast, style, rng, goal, parsed.title, library)
        spec = builder.build(scenes, seed, notes)
        attach_library_gaps(spec, script, cat, notes)
        spec = normalize_spec(
            spec, style=style, seed=seed, target_duration=goal, audio=self.audio, notes=notes,
            catalog=cat, min_scene_sec=2.6, fit_tolerance=0.05,
        )  # fmt: skip
        report = lint_data(spec, catalog=cat, options=LintOptions(check_files=False))
        if not report.ok:
            raise SpecGenerationError(
                "the offline planner produced an invalid spec (a planner bug: please report the "
                "script that triggered it)",
                lint=report,
                spec=spec,
            )
        return GenerationResult(spec, report, attempts=1, notes=notes, generator=self.label)


# =============================================================================== the spec builder
#: decorative background params that may change from scene to scene without breaking continuity
_VARY = ("pattern", "palette", "grid", "marker")


class _Builder:
    def __init__(
        self,
        cat: CatalogView,
        cast: Cast,
        style: str,
        rng: random.Random,
        goal: float,
        title: str | None,
        library: LibraryPlanner | None = None,
    ) -> None:
        self.cat = cat
        self.library = library
        self.scene_texts: dict[str, str] = {}  # scene id -> what its captions say
        self.cast = cast
        self.style = style
        self.rng = rng
        self.goal = goal
        self.title_text = title
        self.tod, self.mood = "day", "neutral"
        self.tod_age = self.mood_age = 99
        self.last_bg: str | None = None
        self.prior_bg: str | None = (
            None  # where the story as a whole happens (the set of its first scene when it names none)
        )
        # the things on the set, scene by scene (needs the library), and how much bigger a small cast is drawn
        self.props: PropTracker | None = None
        self.boost = 1.0
        self.leaving: set[str] = set()  # characters who walked off in the previous scene
        self.introduced: set[str] = set()  # characters seen so far
        self.cam_i = rng.randrange(5)
        self.tr_i = rng.randrange(3)
        self.pal_i = rng.randrange(6)

    # ------------------------------------------------------------------ whole spec
    def build(self, scenes: list[SceneIn], seed: int, notes: list[str]) -> dict[str, Any]:
        self._assign_props(scenes)
        self.prior_bg = self._story_setting(scenes)
        self.boost = self._small_cast_boost()
        if self.library is not None:
            self.props = PropTracker(
                self.library,
                [" ".join(u.text for u in sc.units) for sc in scenes],
                self.boost,
                {c.archetype for c in self.cast.chars},
            )
        all_units = [u for s in scenes for u in s.units]
        title = self._title(all_units)
        n = len(scenes)
        out: list[dict[str, Any]] = []
        prev_cast: list[str] = []
        used: list[str] = []
        for i, sc in enumerate(scenes):
            sc_cast = self._scene_cast(
                sc, prev_cast, self._choose_background(sc, i) if i > 0 else None
            )
            nxt_shout = i + 1 < n and scenes[i + 1].shout_index is not None
            title_i = title if i == 0 else None
            out.append(self._scene(sc, i, n, sc_cast, prev_cast, title_i, out, nxt_shout))
            prev_cast = sc_cast
            used += [c for c in sc_cast if c not in used]
        chars: list[dict[str, Any]] = []
        for c in self.cast.chars:
            if c.id not in used:
                continue
            d: dict[str, Any] = {
                "id": c.id,
                "archetype": c.archetype,
                "palette": c.palette,
                "name": c.name,
            }
            props = [p for p in c.props if p in self.cat.c.props][:1]
            if props:
                d["props"] = props
            chars.append(d)
        bgs = Counter(s["background"]["template"] for s in out)
        notes.append(
            f"offline planner (no LLM): {n} scenes, {len(chars)} character(s) "
            f"({', '.join(c['id'] for c in chars)}), backgrounds "
            + ", ".join(f"{k} x{v}" for k, v in bgs.items())
        )
        return {
            "version": "1.0",
            "meta": {
                "title": title,
                "style": self.style,
                "fps": 30,
                "resolution": [1080, 1920],
                "seed": int(seed),
                "target_duration_sec": self.goal,
                "aspect": "9:16",
            },
            "characters": chars,
            "scenes": out,
        }

    def _title(self, units: list[Unit]) -> str:
        if self.title_text:
            return " ".join(self.title_text.split()[:8])
        first = next((u.text for u in units if u.text), "")
        return _derive_title(first) if first else "Untitled Reel"

    def _assign_props(self, scenes: list[SceneIn]) -> None:
        """Give a character a prop only when the script says they have it ("a kid with a yellow
        backpack", "holding the umbrella"); a prop that is merely mentioned ("where is my umbrella?")
        would otherwise stay in their hand for the whole reel."""
        owners: dict[str, Char] = {}
        for c in self.cast.chars:
            owners[c.name.lower()] = c
            owners[c.id] = c
            if c.role:
                owners[c.role] = c
        for s in scenes:
            for u in s.units:
                toks = u.tokens
                for i, tok in enumerate(toks):
                    prop = _PROP_WORDS.get(tok)
                    if not prop or prop not in self.cat.c.props:
                        continue
                    verb = next(
                        (j for j in range(i - 1, max(-1, i - 5), -1) if toks[j] in _POSSESS), None
                    )
                    if verb is None:
                        continue
                    who = next((owners[t] for t in reversed(toks[:verb]) if t in owners), None)
                    if who is None:
                        who = self.cast.get(u.performer)
                    if who is not None and not who.props:
                        who.props.append(prop)

    def _scene_cast(self, sc: SceneIn, prev: list[str], new_bg: str | None = None) -> list[str]:
        """Who is on screen: everyone the scene's text shows, plus whoever was already there
        (people do not vanish between scenes unless they walked off, and a guest is gone after the scene that named them).
        "They" and "everyone" mean the characters introduced so far, so a surprise guest is not spoiled.  Somebody whose
        trade belongs to a set (the shopkeeper's market, the teacher's classroom) stays on it: when the story moves to another
        set they do not come along unless the scene names them."""
        everyone = [c.id for c in self.cast.chars]
        guests = {c.id for c in self.cast.chars if c.guest}
        order: list[str] = []
        for u in sc.units:
            order += [c for c in u.mentions if c not in order and self.cast.get(c) is not None]
        if any(u.plural for u in sc.units):
            order += [
                c for c in everyone if c in self.introduced and c not in order and c not in guests
            ]

        def stays_behind(cid: str) -> bool:
            ch = self.cast.get(cid)
            home = _ROLE_SETTING.get(ch.archetype) if ch is not None else None
            return bool(home and new_bg and home != new_bg and new_bg != self.last_bg)

        for c in prev:
            if (
                c not in order
                and c not in self.leaving
                and c not in guests
                and not stays_behind(c)
                and len(order) < MAX_ON_SCREEN
            ):
                order.append(c)
        if not order:
            order = [c for c in prev if c not in guests] or [
                next((c for c in everyone if c not in guests), everyone[0])
            ]
        rank = {c: i for i, c in enumerate(everyone)}
        chosen = sorted(order[:MAX_ON_SCREEN], key=lambda c: rank.get(c, 99))
        self.introduced.update(chosen)
        return chosen

    # ------------------------------------------------------------------ one scene
    def _scene(
        self,
        sc: SceneIn,
        i: int,
        n: int,
        sc_cast: list[str],
        prev_cast: list[str],
        title: str | None,
        built: list[dict[str, Any]],
        next_is_shout: bool,
    ) -> dict[str, Any]:
        cats = self.cat.c.caption_styles
        has_title = bool(title) and "title" in cats
        t = 1.1 if has_title else LEAD_SEC + 0.1
        caps: list[dict[str, Any]] = []
        spans: list[tuple[float, float, Unit]] = []
        for u in sc.units:
            length = u.read_sec
            if not u.silent:
                style = "subtitle"
                if u.shout and "shout" in cats:
                    style, length = "shout", max(1.8, length)
                if style not in cats:
                    style = cats.names()[0]
                cap: dict[str, Any] = {
                    "text": u.text,
                    "t0": round(t, 2),
                    "t1": round(t + length, 2),
                    "style": style,
                }
                if u.speaker and self.cast.get(u.speaker) is not None:
                    cap["speaker"] = u.speaker
                if style == "shout":
                    cap["speak"] = True
                caps.append(cap)
            spans.append((t, t + length, u))
            t += length + GAP_SEC
        nat = (t - GAP_SEC + TAIL_SEC) if sc.units else (sc.seconds or BRIDGE_SEC)
        nat = min(MAX_SCENE_SEC, max(MIN_SCENE_SEC, nat))
        if has_title:
            nat = max(nat, 3.6)
            caps.insert(
                0, {"text": title, "t0": 0.4, "t1": round(min(3.4, nat - 0.3), 2), "style": "title"}
            )
        # small animals stand low in the frame, where the subtitles usually are: they are read from the middle instead
        if self.boost > 1.0:
            for cap in caps:
                if cap["style"] != "title":
                    cap["anchor"] = "center"
        scene_text = " ".join(u.text for u in sc.units)
        scene: dict[str, Any] = {
            "id": f"s{i + 1:02d}",
            "duration_sec": round(nat, 2),
            "background": self._background(sc, i),
        }
        self.scene_texts[scene["id"]] = " ".join([title or "", scene_text]).strip()
        layers = self._layers(sc, sc_cast, prev_cast, spans, nat, i == 0, i)
        camera = self._camera(nat, sc)
        if camera:
            scene["camera"] = {"moves": camera}
        scene["layers"] = layers
        if self.props is not None:
            objects = self.props.scene(
                scene["background"]["template"],
                [(s0, e0, u.text, u.performer) for s0, e0, u in spans],
                layers,
                nat,
            )
            if objects:
                scene["objects"] = objects
        scene["captions"] = caps
        sfx = self._splashes(
            self._sfx(layers, caps, nat, built[-1] if built else None), scene.get("objects", [])
        )
        if sfx:
            scene["sfx"] = sfx
        tr = self._transition(i, n, nat, next_is_shout)
        if tr:
            scene["transition_out"] = tr
        return scene

    # ------------------------------------------------------------------ background
    def _place_scores(
        self, bgs: dict[str, Any], text: set[str], said: set[str], named: dict[str, float]
    ) -> tuple[dict[str, float], dict[str, float]]:
        """``(strong, weak)`` evidence for each background from the words of one stretch of the script.

        *Strong* means a place is named ("kitchen", "rooftop", "shop", "street", a library place); *weak* means only a parameter's
        value shows up ("rain" says weather, not place).  A word only ever spoken counts for :data:`SPEECH_SHARE` of what it would in
        narration, and the words that turn up in any story ("song", "desk", "idea") for half; the explainer set (the whiteboard)
        is evidence only in an explainer, because a story that mentions a whiteboard is still in the office."""
        strong: dict[str, float] = {}
        weak: dict[str, float] = {}
        # a word the library can put on the set as an object (a tree, a sofa) says little about the place
        thing_words = set(self.library.objects.single_words()) if self.library else set()

        def seen(word: str) -> float:
            return (
                1.0 if word in text and word not in said else SPEECH_SHARE if word in said else 0.0
            )

        for name, meta in bgs.items():
            dim = 0.4 if name == "whiteboard" and not self.cast.explainer else 1.0
            soft = _WEAK_BG.get(name, frozenset())
            sc = sum(
                seen(kw) * (0.5 if kw in soft else 1.0) * dim
                for kw in _BG_KEYWORDS.get(name, ())
                if kw not in thing_words
            )
            sc += sum(
                2.0 * seen(part) * dim
                for part in re.split(r"[_\W]+", name.lower())
                if len(part) >= 3
            )
            sc += sum(1.0 * seen(tag) * dim for tag in meta["tags"] if len(tag) >= 3)
            sc += named.get(name, 0.0)
            wk = 0.0
            for key, vals in meta["enums"].items():
                if key not in _OWN_PARAMS:
                    wk += sum(1.5 for v in vals if _enum_hit(v, text))
            if sc:
                strong[name] = sc + wk
            elif wk:
                weak[name] = wk
            else:
                thing_hits = sum(1 for kw in _BG_KEYWORDS.get(name, ()) if kw in text)
                if thing_hits:
                    weak[name] = float(thing_hits)
        return strong, weak

    def _named_places(self, told: str, scene_text: str) -> dict[str, float]:
        """The library's places a scene names: in what is told at full weight, in what is only said at :data:`SPEECH_SHARE`."""
        if not self.library:
            return {}
        in_told = self.library.place_scores(told) if told.strip() else {}
        everything = self.library.place_scores(scene_text)
        return {
            n: in_told.get(n, 0.0) + SPEECH_SHARE * max(0.0, v - in_told.get(n, 0.0))
            for n, v in everything.items()
        }

    def _story_setting(self, scenes: list[SceneIn]) -> str | None:
        """The set a story begins on when its first lines name none: the one its title names, else the first one any scene names
        (a story starts where it first says it is), else the one a character's trade implies (a farmer's farm, a chef's kitchen).
        ``None`` for an explainer, which has no set, or when nothing says."""
        bgs = self.cat.backgrounds()
        if self.cast.explainer or not bgs:
            return None
        if self.title_text:
            strong, _weak = self._place_scores(
                bgs,
                set(_words(self.title_text)),
                set(),
                self._named_places(self.title_text, self.title_text),
            )
            top = max(strong.values(), default=0.0)
            if top >= 1.0:
                return sorted(n for n, v in strong.items() if v == top)[0]
        # a place named only near the end ("went home") is where the story goes, not where it starts
        for sc in scenes[: max(1, math.ceil(0.6 * len(scenes)))]:
            toks, text, told, said = self._background_inputs(sc)
            strong, _weak = self._place_scores(bgs, set(toks), said, self._named_places(told, text))
            top = max(strong.values(), default=0.0)
            if top >= SWITCH_SCORE:
                return sorted(n for n, v in strong.items() if v == top)[0]
        trades = Counter(
            _ROLE_SETTING[c.archetype]
            for c in self.cast.chars
            if _ROLE_SETTING.get(c.archetype) in bgs
        )
        if trades:
            top_n = max(trades.values())
            return sorted(n for n, v in trades.items() if v == top_n)[0]
        homes = Counter(_HABITAT.get(c.archetype) for c in self.cast.chars)
        if homes and None not in homes:  # nobody but animals
            top_h = max(homes.values())
            found = sorted(n for n, v in homes.items() if v == top_h and n in bgs)
            if found:
                return found[0]
        return None

    def _background_inputs(self, sc: SceneIn) -> tuple[list[str], str, str, set[str]]:
        """What a scene says, for choosing its set: every word, its caption text, what is told rather than said, and the
        words that are only ever said (in a quote)."""
        tokens = [t for u in sc.units for t in u.tokens]
        text = " ".join(u.text for u in sc.units)
        told = " ".join(u.narr_text for u in sc.units)
        said = {t for u in sc.units for t in u.speech} - {
            t for u in sc.units for t in _words(u.narr_text)
        }
        return tokens, text, told, said

    def _choose_background(self, sc: SceneIn, index: int) -> str:
        """The set a scene is on.  Nothing is remembered here: the choice depends on the last scene's set, which it only reads."""
        bgs = self.cat.backgrounds()
        if not bgs:  # nothing registered: the linter will say so
            return "abstract"
        tokens, scene_text, told, said = self._background_inputs(sc)
        strong, weak = self._place_scores(
            bgs, set(tokens), said, self._named_places(told or scene_text, scene_text)
        )
        home = self.last_bg or (self.prior_bg if index == 0 else None)
        if strong:
            top = max(strong.values())
            pool = sorted(n for n, s_ in strong.items() if s_ == top)
            if home in strong and strong[home] >= top - 1.0:
                return home  # as good as the best: stay where the story is
            if home and top < SWITCH_SCORE:
                return home  # only a hint of another place ("a song", "at her desk"): not a change of set
            return home if home in pool else pool[0]
        if home and (index > 0 or self.prior_bg):
            return home  # no place named in this scene: stay where the story is
        if weak:
            top = max(weak.values())
            return sorted(n for n, s_ in weak.items() if s_ == top)[0]
        return "abstract" if "abstract" in bgs else sorted(bgs)[0]

    def _background(self, sc: SceneIn, index: int) -> dict[str, Any]:
        bgs = self.cat.backgrounds()
        if not bgs:
            return {"template": "abstract"}
        choice = self._choose_background(sc, index)
        self.last_bg = choice
        params = self._bg_params(choice, bgs[choice], set(self._background_inputs(sc)[0]), index)
        return {"template": choice, **({"params": params} if params else {})}

    def _bg_params(
        self, name: str, meta: dict[str, Any], text: set[str], index: int
    ) -> dict[str, Any]:
        enums: dict[str, list[str]] = meta["enums"]
        strong = {k: set(v[0]) for k, v in _TIME_WORDS.items()}
        weak = {k: set(v[1]) for k, v in _TIME_WORDS.items()}
        tod = next((k for k in _TIME_WORDS if text & strong[k]), None)
        if tod is None and not self.cast.explainer:  # "night" in an explainer is a topic, not a set
            tod = next((k for k in _TIME_WORDS if text & weak[k]), None)
        if tod:  # lighting and mood are sticky for a few scenes so a story keeps its atmosphere
            self.tod, self.tod_age = tod, 0
        else:
            self.tod_age += 1
            if self.tod_age > 3:
                self.tod = "day"
        mood = next((k for k, forms in _MOOD_BG.items() if text & set(forms)), None)
        if mood and self.cast.explainer and mood in ("gloomy", "dramatic"):
            mood = None  # an explainer about sad or scary topics should not look sad or scary
        if mood:
            self.mood, self.mood_age = mood, 0
        else:
            self.mood_age += 1
            if self.mood_age > 2:
                self.mood = "neutral"
        params: dict[str, Any] = {}
        if self.tod != "day" and self.tod in enums.get("time_of_day", ()):
            params["time_of_day"] = self.tod
        if self.mood != "neutral" and self.mood in enums.get("mood", ()):
            params["mood"] = self.mood
        for key, vals in enums.items():  # template-specific enums ("room": "kitchen") from the text
            if key not in _OWN_PARAMS:
                hit = next((v for v in vals if _enum_hit(v, text)), None)
                if hit:
                    params[key] = hit
        if self.cast.explainer or name == "abstract":  # slides and title cards may change look
            for key in _VARY:
                vals = [v for v in enums.get(key, ()) if v not in ("auto", "none")]
                if not vals or key in params or (key == "palette" and self.mood != "neutral"):
                    continue
                params[key] = vals[(index + self.pal_i + stable_int("vary", key) % 7) % len(vals)]
        model = meta["model"]
        if isinstance(model, type) and issubclass(model, BaseModel):
            for candidate in (
                params,
                {k: v for k, v in params.items() if k in ("time_of_day", "mood")},
                {},
            ):
                try:
                    model.model_validate(candidate)
                except ValidationError:
                    continue
                return candidate
        return params

    # ------------------------------------------------------------------ layers & actions
    def _slots(self, k: int, index: int) -> list[str]:
        if k == 1 and len(self.cast.chars) == 1:  # a lone presenter moves around the frame
            return [("center", "right", "center", "left")[(index + self.pal_i) % 4]]
        return {1: ["center"], 2: ["left", "right"], 3: ["left", "center", "right"]}[
            min(3, max(1, k))
        ]

    def _layers(
        self,
        sc: SceneIn,
        sc_cast: list[str],
        prev_cast: list[str],
        spans: list[tuple[float, float, Unit]],
        dur: float,
        first_scene: bool,
        index: int,
    ) -> list[dict[str, Any]]:
        cat = self.cat
        slots = self._slots(len(sc_cast), index)
        reqs: dict[str, list[ActionRequest]] = {cid: [] for cid in sc_cast}
        entered: dict[str, float] = {}  # when each arriving character is on screen
        self.leaving = set()
        if cat.has_action("enter_from"):
            entering = [cid for cid in sc_cast if first_scene or cid not in prev_cast]
            for k, cid in enumerate(entering):
                appears = next((s for s, _e, u in spans if cid in u.mentions), None)
                t0 = 0.15 + 0.55 * k
                if not first_scene and appears is not None and appears > 1.4:
                    t0 = max(0.15, appears - 0.5)  # walk in when the story brings them in
                req = enter_request(cat, self._archetype(cid), t0, self.rng)
                if req:
                    reqs[cid].append(req)
                    entered[cid] = t0 + 1.5
        for idx, (s, e, u) in enumerate(spans):
            performer = u.performer if u.performer in reqs else sc_cast[0]
            # a drink is acted only by a picture (an animal bows to the water): for a person the line gets the usual gesture
            cues = [
                c
                for c in u.cues
                if not c.kind.startswith("mood:")
                and not (c.kind == "drink" and not self._is_picture(c.subject or performer or ""))
            ]
            if not cues and not u.silent and not u.shout and self.rng.random() < 0.45:
                cues = [self._flavor(u)]  # nothing happens in the text: keep the cast alive
            for k, cue in enumerate(cues[:2]):
                group = [c for c in sc_cast if c in entered or c in reqs] if u.plural else []
                actors = (
                    group
                    if (u.plural and cue.kind != "exit")
                    else [cue.subject if cue.subject in reqs else performer]
                )
                # "the rat drank, and then the crow drank too": the second one acts when the line has got that far
                late = 0.0
                if k > 0 and cue.subject and cue.subject != cues[0].subject:
                    late = 0.5 * (e - s)
                for who in actors:
                    if cue.kind == "drink":
                        reqs[who].extend(self._drink_requests(who, s + late, sc_cast, slots))
                        continue
                    req = self._cue_request(
                        cue, who, u, s + late, e, sc_cast, slots, idx == len(spans) - 1, u.plural
                    )
                    if req:
                        if req.name == "exit_to":
                            self.leaving.add(who)
                        reqs[who].append(req)
            moods = [c for c in u.cues if c.kind.startswith("mood:")]
            if moods and not cues and cat.has_action("idle"):
                who = moods[0].subject if moods[0].subject in reqs else performer
                params = cat.params("idle", {"mood": moods[0].kind.split(":", 1)[1]})
                if params:
                    reqs[who].append(
                        ActionRequest("idle", s + 0.1, min(2.5, max(1.0, e - s)), params, prio=6)
                    )
            if (
                u.speaker in reqs
                and cat.has_action("look_at")
                and len(sc_cast) > 1
                and not u.silent
            ):
                for cid in sc_cast:  # the listeners watch whoever is talking
                    if cid != u.speaker and s + 0.1 >= entered.get(cid, 0.0):
                        look = listener_request(cat, u.speaker, s + 0.1, e - s)
                        if look:
                            reqs[cid].append(look)
        if sc.shout_index is not None:  # the punchline gets a flourish
            s0, _e0, u0 = spans[sc.shout_index]
            who = u0.performer if u0.performer in reqs else sc_cast[0]
            flourish = flourish_request(cat, s0 + 0.15)
            if flourish:
                reqs[who].append(flourish)
        if sc.kind == "bridge":
            self._bridge_business(reqs, sc_cast, dur)
        for cid in sc_cast:  # a picture has no arms: a wave or a point would only take time
            if self._is_picture(cid):
                reqs[cid] = [r for r in reqs[cid] if r.name not in ARMS_ONLY]
        layers: list[dict[str, Any]] = []
        for pos, cid in zip(slots, sc_cast):
            actions_out = self._schedule(reqs[cid], dur)
            if not actions_out and cat.has_action("idle"):
                params = cat.params("idle", {"mood": "happy"} if self.mood == "playful" else {})
                idle: dict[str, Any] = {
                    "name": "idle",
                    "t0": 0.2,
                    "t1": round(min(dur - 0.2, 2.2), 2),
                }
                if params:
                    idle["params"] = params
                actions_out = [idle]
            layer: dict[str, Any] = {"character": cid, "position": pos, "actions": actions_out}
            if self.boost > 1.0 and self._is_picture(cid):
                layer["scale"] = self.boost
            layers.append(layer)
        return layers

    def _is_picture(self, cid: str) -> bool:
        """Is the character drawn from a library picture (no jointed body: no arms, tips forward to bow)?"""
        return (
            self.library is not None
            and self.library.sprite_roles(self._archetype(cid) or "") is not None
        )

    def _small_cast_boost(self) -> float:
        """How much bigger the cast is drawn.  A library animal is drawn to scale with a person, so a crow stands a tenth of the
        frame tall and a rat a fourteenth.  When nobody in the story is taller than :data:`SMALL_CAST` design px (a person is
        575), the whole cast, and the small things it uses, are drawn up to a third bigger so a scene reads on a phone; three or
        more share a frame that is only so wide.  A cast with a person in it, or a big animal, is left as it is."""
        if self.library is None or self.cast.explainer:
            return 1.0
        heights = []
        for c in self.cast.chars:
            a = (
                self.library.cat.assets.get(c.archetype)
                if c.archetype in self.library.cat.assets
                else None
            )
            heights.append(a.height if a is not None and a.kind == "character" else PERSON_HEIGHT)
        if not heights:
            return 1.0
        grow = max(1.0, min(SMALL_CAST_GROWS, SMALL_CAST / max(heights)))
        return round(min(grow, 1.15) if len(heights) >= 3 else grow, 3)

    def _drink_requests(
        self, who: str, s: float, cast_ids: list[str], slots: list[str]
    ) -> list[ActionRequest]:
        """An animal drinks by stepping up to the water and bowing to it (a person's drink is left to the words)."""
        cat = self.cat
        if not self._is_picture(who) or not cat.has_action("pick_up"):
            return []
        out: list[ActionRequest] = []
        idx = cast_ids.index(who) if who in cast_ids else 0
        x_now = SLOT_X.get(slots[idx], 0.5)
        t = s + 0.15
        if cat.has_action("walk") and abs(x_now - 0.5) > 0.1:
            step = 0.08 if x_now < 0.5 else -0.08
            dx = round(max(0.2 - x_now, min(0.8 - x_now, step)), 2)
            walk = cat.params("walk", {"dx": dx})
            if walk:
                out.append(ActionRequest("walk", t, 1.2, walk, prio=1))
                t += 1.3
        out.append(ActionRequest("pick_up", t, 1.5, cat.params("pick_up", {}), prio=3))
        return out

    def _archetype(self, cid: str) -> str | None:
        c = self.cast.get(cid)
        return c.archetype if c else None

    def _flavor(self, u: Unit) -> Cue:
        """A gesture to match the punctuation when the text has no verb for us."""
        return flavor_cue(u.text, u.performer, self.rng)

    def _cue_request(
        self,
        cue: Cue,
        who: str,
        u: Unit,
        s: float,
        e: float,
        cast_ids: list[str],
        slots: list[str],
        last: bool,
        together: bool,
    ) -> ActionRequest | None:
        idx = cast_ids.index(who) if who in cast_ids else 0
        x_now = SLOT_X.get(slots[idx], 0.5)
        if together:  # a group moves the same way
            toward = 1.0
        elif x_now != 0.5:
            toward = 1.0 if x_now < 0.5 else -1.0  # towards the middle of the frame
        else:
            toward = 1.0 if self.rng.random() < 0.5 else -1.0
        others = [c for c in cast_ids if c != who]
        focus = next((m for m in u.mentions if m != who and m in cast_ids), None)
        return plan_cue(
            self.cat,
            cue,
            start=s + 0.15,
            span=max(1.0, e - s),
            end=e,
            x_now=x_now,
            toward=toward,
            target=focus or (others[0] if others else None),
            last=last,
        )

    def _bridge_business(
        self, reqs: dict[str, list[ActionRequest]], cast_ids: list[str], dur: float
    ) -> None:
        """Silent bridge scenes: the cast ponders, looks around or smiles instead of speaking."""
        for cid, found in bridge_requests(self.cat, cast_ids, dur, self.rng).items():
            reqs[cid].extend(found)

    def _schedule(self, reqs: list[ActionRequest], dur: float) -> list[dict[str, Any]]:
        return schedule_requests(self.cat, reqs, dur)

    # ------------------------------------------------------------------ camera, sfx, transition
    def _camera(self, dur: float, sc: SceneIn) -> list[dict[str, Any]]:
        i = self.cam_i
        self.cam_i += 1
        return plan_camera(self.cat, dur, i, sc.shout_index is not None)

    def _splashes(
        self, sfx: list[dict[str, Any]], objects: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """A thing that goes into another (its move ends as it fades out) is heard going in."""
        name = self.cat.sfx("pop")
        if not name:
            return sfx
        out = list(sfx)
        for ob in objects:
            motions = ob.get("motions", [])
            move = next((m for m in motions if m["type"] == "move"), None)
            goes_in = any(m["type"] == "fade" and m.get("to") == 0.0 for m in motions)
            if move is None or not goes_in:
                continue
            t = round(float(move["t1"]) - 0.05, 2)
            if all(abs(t - float(x["t"])) >= 0.5 for x in out):
                out.append({"name": name, "t": t, "volume": 0.9})
        return sorted(out, key=lambda x: float(x["t"]))

    def _sfx(
        self,
        layers: list[dict[str, Any]],
        caps: list[dict[str, Any]],
        dur: float,
        prev_scene: dict[str, Any] | None,
    ) -> list[dict[str, Any]]:
        prev = (prev_scene.get("transition_out") or {}).get("type") if prev_scene else None
        return plan_sfx(self.cat, layers, caps, dur, prev)

    def _transition(self, i: int, n: int, dur: float, next_is_shout: bool) -> dict[str, Any] | None:
        if i == n - 1 or not self.cat.c.transitions.names():
            return cut_value(self.cat)
        k = self.tr_i
        self.tr_i += 1
        return plan_transition(self.cat, self.style, k, dur, next_is_shout)


__all__ = [
    "Cast",
    "HeuristicSpecGenerator",
    "Parsed",
    "Unit",
    "build_cast",
    "build_units",
    "parse_script",
    "plan_scenes",
]
