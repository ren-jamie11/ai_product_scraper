"""
Sentence attribution — which sentence a tag came from, and which words match.

Tags are paraphrased, not quoted, so this is a scoring problem rather than a string
search. Every tag word is weighted by how rare it is across the parse (a word that
appears in every third sentence, like the product noun, says almost nothing; a word
that appears twice in the whole run says a lot). Each sentence-like unit of the body
is scored by how much of that weight it contains, and the best unit wins if it clears
two bars: enough of the tag is present, and at least one distinctive word is among
what matched. A tag matched only on "frame" is not a match.

Deterministic, no network, no dependencies. Measured on 2,512 tag mentions across the
seven parsed groups on disk (2026-09-16):

    all tags 92.0%  ·  listings 99.9%  ·  reviews 89.0%  ·  English text only 94.1%

with ~97% of confident matches hand-verified as the right sentence. Of the unmatched
8%, roughly a third are non-English reviews (the tagger translates them, so nothing
lexical can reach them) and the rest are true paraphrases. Both fall back to the full
body: a wrong sentence is worse than no sentence.

Run `python -m pipeline.attribution <slug> <run_id> [parse_id]` to print that table for
one parse. Do it before and after touching STOP, SCAFFOLD, SYN or the thresholds.
"""

from __future__ import annotations

import functools
import math
import re
import sys
from collections import Counter

# A unit is confident when at least this share of the tag's weighted words is present
# and the strongest matched word has at least this share of the maximum possible
# rarity — or when every word of the tag is present, whatever the words are.
THRESH = 0.4
DISTINCT = 0.4

# Words the tagger injects that the source rarely says: completion nouns ("sturdy" ->
# "sturdy build"), normalised approval ("we like the color" -> "nice color") and
# tag-speak ("suitable for", "required"). They count a little, never a lot.
SCAFFOLD_W = 0.25
SCAFFOLD = frozenset("""
build construction finish material materials design quality look looks looking feel
appearance surface option options feature features style
nice good great pretty beautiful perfect lovely excellent suitable required needed
requires needs multiple various overall provides provide included includes include
""".split())

STOP = frozenset("""
a about above after again against all am an and any are aren as at be because been
before being below between both but by can cannot could couldn did didn do does doesn
doing don down during each few for from further had hadn has hasn have haven having he
her here hers herself him himself his how i if in into is isn it its itself just let me
more most mustn my myself no nor not of off on once only or other ought our ours
ourselves out over own same shan she should shouldn so some such than that the their
theirs them themselves then there these they this those through to too under until up
very was wasn we were weren what when where which while who whom why will with won
would wouldn you your yours yourself yourselves
dont doesnt didnt isnt wasnt cant wont im ive youre theyre thats ill id itll
also really quite even much many lot lots get got gets getting thing things one ones
way bit little us
product item bought purchase purchased buy buying ordered order came come comes arrived
""".split())

# Stem-level folds the stemmer cannot see through, applied after stemming so every
# inflection lands on one side. Kept deliberately short: only pairs a shopper would
# never distinguish.
SYN = {
    "leav": "leaf", "shelv": "shelf",
    "big": "larg", "bigger": "larg",
    "tini": "small", "smaller": "small",
    "pictur": "photo", "pic": "photo", "imag": "photo",
    "lightweight": "light",
    "faux": "fake", "artifici": "fake",
    "colour": "color",
}

# Matched on the ORIGINAL text so every offset is exact. Apostrophes stay inside the
# token ("don't") and are dropped from the token string only, never from the text.
_TOKEN = re.compile(r"[A-Za-z]+(?:['’][A-Za-z]+)?|\d+(?:\.\d+)?")
_APOS = re.compile(r"['’]")

# A unit ends at . ! ? ; followed by whitespace or straight by a capital ("out.The"
# is common in reviews), or at a spaced separator. Bullet headers ("EASY TO CLEAN:
# ...") are split off when short enough to be a header rather than a sentence.
_SPLIT = re.compile(r"(?<=[.!?;])(?:\s+|(?=[A-Z]))|\s+[|•]\s+|\s+[-–—]\s+")
_HEADER = re.compile(r"^([^:.!?]{2,80}?):\s+")
_ALNUM = re.compile(r"[A-Za-z0-9]")


# ---------------------------------------------------------------------------
# Porter stemmer — the standard algorithm, nothing more
# ---------------------------------------------------------------------------

def _cons(w: str, i: int) -> bool:
    c = w[i]
    if c in "aeiou":
        return False
    if c == "y":
        return i == 0 or not _cons(w, i - 1)
    return True


def _m(w: str) -> int:
    n = i = 0
    length = len(w)
    while i < length and _cons(w, i):
        i += 1
    while i < length:
        while i < length and not _cons(w, i):
            i += 1
        if i >= length:
            break
        n += 1
        while i < length and _cons(w, i):
            i += 1
    return n


def _vowel(w: str) -> bool:
    return any(not _cons(w, i) for i in range(len(w)))


def _dbl(w: str) -> bool:
    return len(w) >= 2 and w[-1] == w[-2] and _cons(w, len(w) - 1)


def _cvc(w: str) -> bool:
    if len(w) < 3:
        return False
    return (_cons(w, len(w) - 1) and not _cons(w, len(w) - 2)
            and _cons(w, len(w) - 3) and w[-1] not in "wxy")


_STEP2 = [("ational", "ate"), ("tional", "tion"), ("enci", "ence"), ("anci", "ance"),
          ("izer", "ize"), ("abli", "able"), ("alli", "al"), ("entli", "ent"), ("eli", "e"),
          ("ousli", "ous"), ("ization", "ize"), ("ation", "ate"), ("ator", "ate"),
          ("alism", "al"), ("iveness", "ive"), ("fulness", "ful"), ("ousness", "ous"),
          ("aliti", "al"), ("iviti", "ive"), ("biliti", "ble")]
_STEP3 = [("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"), ("ical", "ic"),
          ("ful", ""), ("ness", "")]
_STEP4 = ["ement", "ance", "ence", "able", "ible", "ment", "ant", "ent", "ion", "ism",
          "ate", "iti", "ous", "ive", "ize", "al", "er", "ic", "ou"]


@functools.lru_cache(maxsize=None)
def stem(w: str) -> str:
    # Pure function of a short string. A parse has a few thousand distinct words, so
    # the cache stays small and is shared across parses for the life of the server.
    if len(w) <= 2:
        return w
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("ies"):
        w = w[:-2]
    elif w.endswith("ss"):
        pass
    elif w.endswith("s"):
        w = w[:-1]

    if w.endswith("eed"):
        if _m(w[:-3]) > 0:
            w = w[:-1]
    else:
        stripped = False
        if w.endswith("ed") and _vowel(w[:-2]):
            w, stripped = w[:-2], True
        elif w.endswith("ing") and _vowel(w[:-3]):
            w, stripped = w[:-3], True
        if stripped:
            if w.endswith(("at", "bl", "iz")):
                w += "e"
            elif _dbl(w) and w[-1] not in "lsz":
                w = w[:-1]
            elif _m(w) == 1 and _cvc(w):
                w += "e"

    if w.endswith("y") and _vowel(w[:-1]):
        w = w[:-1] + "i"

    for suffix, repl in _STEP2:
        if w.endswith(suffix):
            if _m(w[:-len(suffix)]) > 0:
                w = w[:-len(suffix)] + repl
            break
    for suffix, repl in _STEP3:
        if w.endswith(suffix):
            if _m(w[:-len(suffix)]) > 0:
                w = w[:-len(suffix)] + repl
            break
    for suffix in _STEP4:
        if w.endswith(suffix):
            base = w[:-len(suffix)]
            if suffix == "ion":
                if base and base[-1] in "st" and _m(base) > 1:
                    w = base
            elif _m(base) > 1:
                w = base
            break

    if w.endswith("e"):
        measure = _m(w[:-1])
        if measure > 1 or (measure == 1 and not _cvc(w[:-1])):
            w = w[:-1]
    if _m(w) > 1 and _dbl(w) and w.endswith("l"):
        w = w[:-1]
    return w


# ---------------------------------------------------------------------------
# Text -> tokens and units
# ---------------------------------------------------------------------------

def tokens(text: str) -> list[tuple[str, int, int, str]]:
    """(stem, start, end, raw) for every content word, offsets into `text`."""
    out = []
    for m in _TOKEN.finditer(text):
        raw = _APOS.sub("", m.group(0)).lower()
        if raw in STOP or (len(raw) == 1 and not raw.isdigit()):
            continue
        st = stem(raw)
        out.append((SYN.get(st, st), m.start(), m.end(), raw))
    return out


def units(text: str) -> list[tuple[int, int]]:
    """(start, end) of every sentence-like unit, offsets into `text`."""
    out: list[tuple[int, int]] = []
    pos = 0
    for line in text.split("\n"):
        start, pos = pos, pos + len(line) + 1
        if not line.strip():
            continue
        parts: list[tuple[int, int]] = []
        cursor = 0
        header = _HEADER.match(line)
        if header and len(header.group(1).split()) <= 8:
            parts.append((0, header.end(1)))
            cursor = header.end()
        last = cursor
        for split in _SPLIT.finditer(line, cursor):
            if split.start() > last:
                parts.append((last, split.start()))
            last = split.end()
        if last < len(line):
            parts.append((last, len(line)))
        for a, b in parts:
            segment = line[a:b]
            lead = len(segment) - len(segment.lstrip())
            trail = len(segment) - len(segment.rstrip())
            if b - trail > a + lead:
                out.append((start + a + lead, start + b - trail))
    return out


def _same_word(t: str, s: str) -> bool:
    """Two stems that are one word for our purposes.

    wood/wooden, real/realist and stack/stackabl are prefix pairs; shine/shini differ
    only in a final vowel the stemmer left behind. Deliberately no looser rule: a
    shared-prefix test let "resin" match "resist".
    """
    if t == s:
        return True
    short, long = (t, s) if len(t) <= len(s) else (s, t)
    if len(short) >= 4 and long.startswith(short) and len(long) - len(short) <= 3:
        return True
    return (len(short) >= 5 and len(t) == len(s) and t[:-1] == s[:-1]
            and t[-1] in "aeiou" and s[-1] in "aeiou")


# ---------------------------------------------------------------------------
# The matcher
# ---------------------------------------------------------------------------

def no_match() -> dict:
    return {"confident": False, "score": 0.0, "distinct": 0.0, "unit": None, "spans": []}


class Attributor:
    """Sentence attribution over one parse's bodies.

    Built once per parse because word rarity is measured across every sentence in the
    run — the same tag scores differently against a mug run and a frame run. Every unit
    is tokenised once here too, because `attribute` runs for every mention in the parse
    (1,200 on a 12-ASIN group) and re-stemming each sentence per mention was 93% of the
    results build (measured 2026-09-18: 1.24 s → 0.18 s once cached, identical output).
    """

    def __init__(self, bodies: list[dict]):
        self.text: dict[str, str] = {}
        self.units: dict[str, list[tuple[int, int]]] = {}
        self.unit_tokens: dict[str, list[list[tuple[str, int, int, str]]]] = {}
        df: Counter = Counter()
        total = 0
        for body in bodies:
            body_id, text = body["body_id"], body.get("text") or ""
            self.text[body_id] = text
            self.units[body_id] = units(text)
            self.unit_tokens[body_id] = [tokens(text[a:b]) for a, b in self.units[body_id]]
            for unit_tokens in self.unit_tokens[body_id]:
                total += 1
                for st in {t[0] for t in unit_tokens}:
                    df[st] += 1
        self.idf = {st: math.log((total + 1) / (n + 1)) + 1 for st, n in df.items()}
        self.idf_max = math.log(total + 1) + 1

    def attribute(self, body_id: str, tag: str) -> dict:
        """Best unit for `tag` inside `body_id`, or no_match()."""
        text = self.text.get(body_id)
        if text is None:
            return no_match()

        weights: dict[str, float] = {}
        scaffold: set[str] = set()
        for st, _, _, raw in tokens(tag):
            if st in weights:
                continue
            weights[st] = self.idf.get(st, self.idf_max) * (SCAFFOLD_W if raw in SCAFFOLD else 1.0)
            if raw in SCAFFOLD:
                scaffold.add(st)
        if not weights:
            return no_match()
        total = sum(weights.values())

        best = None
        for (a, b), unit_tokens in zip(self.units[body_id], self.unit_tokens[body_id]):
            if not unit_tokens:
                continue
            matched: set[str] = set()
            spans: list[tuple[int, int]] = []
            for st, s0, s1, _ in unit_tokens:
                for t in weights:
                    if _same_word(t, st):
                        matched.add(t)
                        spans.append((a + s0, a + s1))
                        break
            if not matched:
                continue
            score = sum(weights[t] for t in matched) / total
            # Ties: more matched words, then a real sentence over a two-word title,
            # then the shorter (denser) unit, then the earlier one.
            key = (round(score, 6), len(matched),
                   -len(unit_tokens) if len(unit_tokens) > 2 else -999, -a)
            if best is None or key > best[0]:
                best = (key, (a, b), score, matched, spans)

        if best is None:
            return no_match()
        _, unit, score, matched, spans = best

        distinct = max(
            (self.idf.get(t, self.idf_max) for t in matched if t not in scaffold),
            default=0.0,
        ) / self.idf_max
        confident = score >= 0.999 or (score >= THRESH and distinct >= DISTINCT)

        spans.sort()
        merged: list[list[int]] = []
        for s0, s1 in spans:
            if merged and not _ALNUM.search(text[merged[-1][1]:s0]):
                merged[-1][1] = s1
            else:
                merged.append([s0, s1])

        return {
            "confident": confident,
            "score": round(score, 3),
            "distinct": round(distinct, 3),
            "unit": list(unit) if confident else None,
            "spans": merged if confident else [],
        }


LISTS = ("features", "complaints", "assembly_maintenance")


# ---------------------------------------------------------------------------
# CLI — the regression report
# ---------------------------------------------------------------------------

_ENGLISH = frozenset("the and a to of it is for in this with my on that they are but was "
                     "have so not these very i you as be or at them one all".split())


def _looks_english(text: str) -> bool:
    words = re.findall(r"[A-Za-z]+", text.lower())
    return bool(words) and sum(w in _ENGLISH for w in words) / len(words) >= 0.08


def _marked(text: str, unit, spans) -> str:
    a, b = unit
    out, pos = "", a
    for s0, s1 in spans:
        out += text[pos:s0] + "[[" + text[s0:s1] + "]]"
        pos = s1
    return out + text[pos:b]


def report(tagged: dict, samples: int = 25) -> str:
    """The accuracy table plus random marked samples, as printable text."""
    import random

    bodies = [b for b in tagged.get("bodies") or [] if b.get("status") == "ok"]
    attributor = Attributor(bodies)
    rows = []
    for body in bodies:
        english = _looks_english(body.get("text") or "")
        for key in LISTS:
            for tag in (body.get("tags") or {}).get(key) or []:
                result = attributor.attribute(body["body_id"], tag)
                rows.append((key, body["type"], english, tag, body["text"], result))
    if not rows:
        return "No tags in this parse."

    def pct(subset) -> str:
        subset = list(subset)
        if not subset:
            return "   —  "
        return f"{sum(r[5]['confident'] for r in subset) / len(subset) * 100:5.1f}%"

    lines = [f"{len(rows)} tag mentions · confident {pct(rows)} · "
             f"exact {sum(r[5]['score'] >= 0.999 for r in rows) / len(rows) * 100:.1f}%"]
    for key in LISTS:
        sub = [r for r in rows if r[0] == key]
        if sub:
            lines.append(f"  {key:22s} n={len(sub):4d}  confident {pct(sub)}")
    for typ in ("listing", "review"):
        sub = [r for r in rows if r[1] == typ]
        if sub:
            lines.append(f"  {typ:22s} n={len(sub):4d}  confident {pct(sub)}")
    english = [r for r in rows if r[2]]
    lines.append(f"  {'English text only':22s} n={len(english):4d}  confident {pct(english)}")
    missed = [r for r in rows if not r[5]["confident"]]
    if missed:
        foreign = sum(1 for r in missed if not r[2])
        lines.append(f"  not confident: {len(missed)}, of which non-English bodies: {foreign}")

    random.seed(7)
    lines.append("")
    lines.append(f"{samples} random confident matches:")
    for key, typ, _, tag, text, result in random.sample(
            [r for r in rows if r[5]["confident"]], min(samples, len(rows))):
        marked = _marked(text, result["unit"], result["spans"]).replace("\n", " ")
        lines.append(f"  [{result['score']:.2f}] {typ[:4]} \"{tag}\"\n      -> {marked[:200]}")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    from pipeline import storage

    if len(argv) < 2:
        print("usage: python -m pipeline.attribution <slug> <run_id> [parse_id] [--samples N]")
        return 2
    slug, run_id = argv[0], argv[1]
    samples = 25
    rest = list(argv[2:])
    if "--samples" in rest:
        at = rest.index("--samples")
        samples = int(rest[at + 1])
        del rest[at:at + 2]

    parses = storage.list_parses(slug, run_id)
    if not parses:
        print(f"No parses under data/{slug}/{run_id}.")
        return 1
    parse_id = rest[0] if rest else parses[0]["id"]
    tagged = storage.read_json(storage.run_dir(slug, run_id) / parse_id / "tagged.json")
    if not tagged:
        print(f"{parse_id} has no tagged.json.")
        return 1
    print(f"data/{slug}/{run_id}/{parse_id}")
    print(report(tagged, samples))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
