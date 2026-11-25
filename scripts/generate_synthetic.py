"""Deterministic, seeded, template-based synthetic chat generator (no LLM).

Safety: every template is non-explicit. Escalation-stage lines are *signals only* (requests for
"private pics", video calls, meeting in person, home-alone questions, gifts for favors). Nothing
here describes sexual acts and nothing should ever be added that does.

Output: data/synthetic/{train,val,test}.jsonl plus manifest.json. Each line is a conversation:
  {id, platform, label: benign|hard_negative|grooming, category, max_stage, speed, split,
   child, messages: [{idx, sender, is_child, text, timestamp, stage}]}

Per-message `stage` labels apply to the other party's messages; the child's messages are NONE.
About 25% of templates in each pool are "held out": they appear only in test conversations,
so evaluation measures generalization to unseen phrasings rather than template recall.

Usage: python scripts/generate_synthetic.py [--n 600] [--seed 1337] [--out data/synthetic]
"""

from __future__ import annotations

import argparse
import json
import random
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PLATFORMS = ["Roblox", "Minecraft", "Mobile Legends", "Fortnite", "Discord", "Messenger"]
GAMES = {
    "Roblox": ["the obby", "bedwars", "the tycoon", "tower defense", "the horror map"],
    "Minecraft": ["skyblock", "the survival world", "bedwars", "the realm", "hardcore"],
    "Mobile Legends": ["ranked", "classic", "brawl", "rank push", "the event mode"],
    "Fortnite": ["zero build", "squads", "creative", "box fights", "the new season"],
    "Discord": ["minecraft", "roblox", "valorant", "the new game", "among us"],
    "Messenger": ["ml", "roblox", "minecraft", "fortnite", "cod mobile"],
}
CURRENCY = {
    "Roblox": "robux",
    "Minecraft": "minecoins",
    "Mobile Legends": "diamonds",
    "Fortnite": "v-bucks",
    "Discord": "nitro",
    "Messenger": "load",
}
ITEMS = ["skin", "sword", "pet", "emote", "hero skin", "glider", "pickaxe", "backpack"]
APPS = ["snap", "telegram", "whatsapp", "insta", "kik", "signal"]
PLACES = ["the mall", "the park", "sm", "the plaza", "the station", "the arcade"]
CITIES = ["Quezon City", "Cebu", "Davao", "Pasig", "Iloilo", "Baguio"]
SCHOOLS = [
    "Riverside Elementary",
    "Maple Grove Academy",
    "Sunrise National High School",
    "Hillview Middle School",
]
ADJ = [
    "shadow",
    "pixel",
    "turbo",
    "frost",
    "lucky",
    "neon",
    "silent",
    "cosmic",
    "mega",
    "tiny",
    "epic",
    "lazy",
]
NOUN = ["wolf", "ninja", "gamer", "panda", "blaze", "knight", "fox", "dragon", "slayer", "builder", "cat"]

STAGES = [
    "FRIENDSHIP",
    "RISK_ASSESSMENT",
    "TRUST_EXCLUSIVITY",
    "ISOLATION",
    "SECRECY",
    "PLATFORM_MOVE",
    "ESCALATION",
]

# ----------------------------------------------------------------------------------------------
# Grooming-stage templates (other party). Slots: {game} {currency} {amount} {item} {app} {place}
# ----------------------------------------------------------------------------------------------
STAGE_TEMPLATES: dict[str, list[str]] = {
    "FRIENDSHIP": [
        "you're really good at {game}",
        "want me to carry you in {game}?",
        "i'll give you {amount} {currency}, no big deal",
        "you're so fun to play with honestly",
        "we should play again tomorrow, just us",
        "i can help you get past that level",
        "nice build! you're super talented",
        "free {currency} if you want, i have extra",
        "i'll gift you the {item}, you deserve it",
        "you're my favorite person to play with",
        "i always have fun when you're online",
        "want the new {item}? i can get it for you",
        "you're way better than the other players here",
        "i bought you something in the shop, check your inbox",
    ],
    "RISK_ASSESSMENT": [
        "how old are you btw?",
        "what grade are you in?",
        "where do you live? what city?",
        "what school do you go to?",
        "do your parents check your phone?",
        "is this your own phone or a shared one?",
        "are your parents home right now?",
        "who else uses your tablet?",
        "when do your parents get home from work?",
        "do your parents know you play online with people?",
        "you sound young, are you like 12?",
        "does your mom ever look at your messages?",
        "which part of town are you from",
        "do you have your own room?",
    ],
    "TRUST_EXCLUSIVITY": [
        "you're so mature for your age",
        "honestly only i understand you",
        "you can tell me anything, i won't judge",
        "you're not like other kids, you're special",
        "i trust you more than anyone",
        "we have a special connection you know",
        "no one gets you like i do",
        "you're special to me, you know that right",
        "you're the only one i can really talk to",
        "i feel like i've known you forever",
        "you act way older than your age",
        "i'd do anything for you, seriously",
    ],
    "ISOLATION": [
        "your parents don't understand you like i do",
        "your friends seem fake tbh",
        "you don't need them, you have me",
        "let's talk after everyone goes to sleep",
        "come online at midnight, it's quieter",
        "it's better when it's just the two of us",
        "your mom is too strict, that's not fair to you",
        "your friends are immature, i'm way more fun",
        "stay up late tonight so we can talk",
        "your family never listens to you anyway",
        "they wouldn't get it, only i would",
        "skip hanging out with them, talk to me instead",
    ],
    "SECRECY": [
        "don't tell anyone we talk ok?",
        "this is our little secret",
        "delete this chat after you read it",
        "keep this between us",
        "no one needs to know about us",
        "don't tell your mom about me, she'd overreact",
        "you can't tell your friends about this",
        "turn on disappearing messages ok",
        "promise you won't show this to anyone",
        "let's keep our chats private, just ours",
        "if anyone asks, we're just teammates",
        "clear the chat history before your parents see",
    ],
    "PLATFORM_MOVE": [
        "add me on {app}, it's more private",
        "what's your {app}?",
        "let's talk on {app} instead, this game chat is monitored",
        "can i get your number? easier to text",
        "do you have telegram?",
        "we should move somewhere more private",
        "message me on {app}, i'll send you my username",
        "this app is lame, let's switch to {app}",
        "give me your number so we can text",
        "do you have {app}? add me there",
        "the chat here gets checked, let's use {app}",
        "i made a private server just for us",
    ],
    "ESCALATION": [
        "send me a pic of you, just for me",
        "are you home alone this weekend?",
        "let's meet at {place}, don't tell your mom",
        "can we video call tonight? just us",
        "i can pick you up after school, it'll be fun",
        "i'll send you a {amount} {currency} gift card if you send a selfie",
        "is anyone else home right now?",
        "turn your camera on for me later ok",
        "where can we meet? i'm near {place}",
        "i'll get you {amount} {currency} if you call me tonight",
        "send a private pic, i won't show anyone",
        "we should meet in person, i'll buy you food",
        "i wanna see what you look like for real",
        "when will you be alone? i'll call",
    ],
}

CHILD_REPLIES: dict[str, list[str]] = {
    "FRIENDSHIP": ["thanks!", "lol ok", "really? thank you!!", "sure", "omg thanks", "ok cool", "yeah!"],
    "RISK_ASSESSMENT": [
        "{age}",
        "i'm {age}",
        "{grade} grade",
        "i live in {city}",
        "i go to {school}",
        "no they never check",
        "my mom sometimes checks",
        "it's my own phone",
        "they're at work",
        "why?",
        "my sister uses it too",
    ],
    "TRUST_EXCLUSIVITY": [
        "aww thanks",
        "i guess",
        "you're nice",
        "lol really?",
        "thanks that means a lot",
        "idk",
    ],
    "ISOLATION": [
        "yeah my parents are annoying sometimes",
        "idk",
        "maybe",
        "my friends are ok",
        "lol ok",
        "i'll try",
    ],
    "SECRECY": ["ok i won't", "why?", "ok", "sure", "is that bad?", "ok i promise"],
    "PLATFORM_MOVE": [
        "i don't have {app}",
        "ok what's yours",
        "my mom doesn't let me have that",
        "maybe later",
        "ok",
        "why?",
    ],
    "ESCALATION": [
        "idk",
        "maybe",
        "i'm not sure",
        "why do you want that?",
        "i have to ask my mom",
        "um ok",
        "i don't know",
    ],
}

FILLER_OTHER = [
    "gg",
    "nice one",
    "lol",
    "wanna play {game}?",
    "my wifi is so bad today",
    "brb",
    "ok back",
    "that boss was hard",
    "did you see the new update?",
    "haha",
    "same",
    "ok",
    "let's go again",
    "that was close",
    "you on later?",
    "what level are you",
    "i need to grind more",
]
FILLER_CHILD = [
    "gg",
    "lol",
    "yeah",
    "ok",
    "brb",
    "back",
    "nice",
    "same",
    "haha",
    "ok lets go",
    "wait",
    "my mom is calling",
    "that was fun",
    "i'm lagging",
    "one more game",
    "i have homework later",
]

# ----------------------------------------------------------------------------------------------
# Benign scenarios: (other, child) line pools.
# ----------------------------------------------------------------------------------------------
BENIGN: dict[str, tuple[list[str], list[str]]] = {
    "gaming_chat": (
        [
            "nice shot!",
            "gg wp",
            "wanna queue again?",
            "my wifi is lagging",
            "go left, they're camping",
            "i need heals",
            "push mid",
            "that update broke the map lol",
            "who's playing {game} later?",
            "i almost had them",
            "you carried that one",
            "ok last game then i sleep",
            "let's try {game}",
            "my brother keeps stealing my controller",
            "the new event is so good",
        ],
        [
            "gg",
            "lol yes",
            "ok one more",
            "i'm low hp",
            "omw",
            "nice",
            "i'm lagging too",
            "that was epic",
            "let's go",
            "wait for me",
            "ok last one",
            "i got the {item}!",
        ],
    ),
    "trash_talk": (
        [
            "you're so bad lol",
            "get rekt",
            "ez",
            "rematch? you got lucky",
            "noob move",
            "skill issue",
            "i was lagging that doesn't count",
            "1v1 me",
            "you'll never beat my score",
            "uninstall lol jk",
        ],
        [
            "lol no u",
            "ez for me",
            "rematch now",
            "you're the noob",
            "cope",
            "i'm literally better",
            "ok 1v1 then",
            "stop crying lol",
        ],
    ),
    "trading": (
        [
            "trade my {item} for your {item}?",
            "add more and it's a deal",
            "no scam right?",
            "deal",
            "i'll put it in the trade window",
            "that's a fair trade",
            "how much is your {item} worth",
            "accept the trade",
            "thanks for trading",
            "do you have the rare {item}?",
        ],
        [
            "ok deal",
            "add one more",
            "no scams",
            "sent",
            "accepted",
            "nah that's unfair",
            "ok fine",
            "it's worth more than that",
            "trade done",
        ],
    ),
    "coordinate_match": (
        [
            "game at 7?",
            "bring your cousin, we need 5",
            "i'll make the lobby",
            "code is 4KX2",
            "who's tank",
            "everyone ready?",
            "start in 5 mins",
            "practice for the school tournament tomorrow",
            "join the voice channel",
            "we need one more player",
        ],
        [
            "ok i'm in",
            "i'll play support",
            "joining",
            "ready",
            "wait 2 mins",
            "i can only play till 8",
            "my cousin can't today",
            "ok see you there",
        ],
    ),
    "parent_checkin": (
        [
            "did you eat lunch?",
            "come home by 6 ok",
            "who are you playing with?",
            "love you, be safe",
            "did you finish your homework?",
            "dinner's ready in 10",
            "don't stay up too late",
            "remember to charge your phone",
            "don't tell your sister about the surprise party ok",
            "grandma says hi",
            "tell me if anyone online is weird to you ok?",
            "are you home yet?",
        ],
        [
            "yes mom",
            "ok",
            "just my classmates",
            "love you too",
            "almost done",
            "coming",
            "ok i won't",
            "ok i promise",
            "yes",
            "i'm home",
        ],
    ),
    "siblings": (
        [
            "mom says dinner",
            "give back my charger",
            "stop using my account",
            "you ate my snack",
            "can i play after you",
            "i'm telling mom lol",
            "turn down the volume",
            "lend me your headphones",
            "you're on my spot",
            "dad's picking us up at 5",
        ],
        [
            "no it's mine",
            "ok wait",
            "5 more mins",
            "you started it",
            "fine",
            "ok",
            "i didn't do it",
            "stop",
            "ok ok",
        ],
    ),
}

HARD_NEGATIVE: dict[str, tuple[list[str], list[str]]] = {
    "coach_teacher": (
        [
            "practice is at 4pm on saturday",
            "tell your parents to sign the permission form",
            "how old are you, for the league registration form?",
            "what grade are you in? need it for the league division",
            "the class video call is at 9 tomorrow",
            "submit your project on the class page",
            "please ask your parents to email me",
            "great job at practice today",
            "bring water and your jersey",
            "the team meeting is on the school field",
            "remind your mom about the tryouts schedule",
            "everyone's parents are invited to the game",
            "good effort today, keep it up",
        ],
        [
            "ok coach",
            "yes sir",
            "ok ma'am",
            "my mom will sign it",
            "i'm in 6th grade",
            "thank you",
            "ok i'll tell my dad",
            "see you saturday",
        ],
    ),
    "kids_age_sharing": (
        [
            "how old are you?",
            "i'm 12 too!",
            "what grade are you in",
            "same grade lol",
            "what school do you go to?",
            "no way i go there too",
            "who's your teacher",
            "our class has the best teacher",
            "are you in 6th?",
            "my sister is 10",
            "we should be in the same team",
        ],
        ["12, you?", "6th grade", "same!", "omg", "Ms. Reyes", "lol yes", "i'm 11 actually", "cool"],
    ),
    "discord_known_friends": (
        [
            "add me on discord, same name as my roblox",
            "join the class server, everyone's there",
            "what's your discord? i'll add you to our group",
            "see you at school tomorrow",
            "my mom said i can use discord now",
            "our group chat is so funny",
            "the whole team is on discord",
            "i'll send the invite to the class server",
            "bring your brother too",
            "it's the same server as last year",
        ],
        [
            "ok adding you",
            "joined!",
            "it's the same as my game name",
            "cool see you tomorrow",
            "lol yes",
            "my dad set it up",
            "ok",
        ],
    ),
    "team_captain": (
        [
            "what's your username so i can invite you to the clan?",
            "join our clan discord for the tournament",
            "what's your discord? for the tournament bracket",
            "we practice every friday",
            "you got accepted to the clan!",
            "read the clan rules pls",
            "everyone check in before the match",
            "our clan is top 50 now",
            "can you play support this week?",
            "good job everyone",
        ],
        ["it's {handle}", "thanks!", "ok i read them", "yes i can", "joined", "let's goo", "ok captain"],
    ),
}

BENIGN_CATS = list(BENIGN)
HN_CATS = list(HARD_NEGATIVE)


def _split_pool(pool: list[str]) -> tuple[list[str], list[str]]:
    """Return (seen, heldout): the last ~25% of each pool is test-only."""
    k = max(1, len(pool) // 4) if len(pool) >= 4 else 0
    return pool[: len(pool) - k], pool[len(pool) - k :]


def _pool(pool: list[str], split: str) -> list[str]:
    seen, held = _split_pool(pool)
    return seen + held if split == "test" else seen


class Gen:
    def __init__(self, rng: random.Random, platform: str, split: str) -> None:
        self.rng = rng
        self.platform = platform
        self.split = split
        self.slots = {
            "game": rng.choice(GAMES[platform]),
            "currency": CURRENCY[platform],
            "amount": rng.choice(["100", "200", "500", "800", "1000"]),
            "item": rng.choice(ITEMS),
            "app": rng.choice(APPS),
            "place": rng.choice(PLACES),
            "age": str(rng.choice([10, 11, 12, 13, 14])),
            "grade": rng.choice(["5th", "6th", "7th", "8th"]),
            "city": rng.choice(CITIES),
            "school": rng.choice(SCHOOLS),
            "handle": handle(rng),
        }

    def fill(self, template: str) -> str:
        text = template.format(**self.slots)
        return self.style(text)

    def style(self, text: str) -> str:
        r = self.rng.random()
        if r < 0.15:
            text = text.replace("you're", "ur").replace("your", "ur").replace("you", "u")
        elif r < 0.22:
            text = text.replace("'", "")
        if self.rng.random() < 0.12:
            text = self.rng.choice(["hey ", "btw ", "ok ", "lol "]) + text
        if self.rng.random() < 0.12:
            text = text + self.rng.choice([" :)", " lol", " haha", "!!", " ok?"])
        if self.rng.random() < 0.05:
            text = text.capitalize()
        return text

    def pick(self, pool: list[str]) -> str:
        return self.fill(self.rng.choice(_pool(pool, self.split)))


def handle(rng: random.Random) -> str:
    return f"{rng.choice(ADJ)}{rng.choice(NOUN)}{rng.randint(1, 999)}"


class Clock:
    def __init__(self, rng: random.Random, start: datetime) -> None:
        self.rng = rng
        self.t = start

    def tick(self, lo: float = 5, hi: float = 90) -> str:
        self.t += timedelta(seconds=self.rng.uniform(lo, hi))
        return self.t.isoformat(timespec="seconds")

    def gap(self, hours_lo: float, hours_hi: float) -> None:
        self.t += timedelta(hours=self.rng.uniform(hours_lo, hours_hi))


def _msg(msgs: list[dict], sender: str, is_child: bool, text: str, ts: str, stage: str = "NONE") -> None:
    msgs.append(
        {
            "idx": len(msgs),
            "sender": sender,
            "is_child": is_child,
            "text": text,
            "timestamp": ts,
            "stage": stage,
        }
    )


def gen_pooled(
    rng: random.Random,
    g: Gen,
    clock: Clock,
    pools: tuple[list[str], list[str]],
    child: str,
    other: str,
    n: int,
) -> list[dict]:
    other_pool, child_pool = pools
    msgs: list[dict] = []
    for _ in range(n):
        if rng.random() < 0.5:
            _msg(msgs, other, False, g.pick(other_pool), clock.tick())
        else:
            _msg(msgs, child, True, g.pick(child_pool), clock.tick())
        if rng.random() < 0.05:
            clock.gap(1, 20)
    return msgs


def stage_path(rng: random.Random, max_stage: str) -> list[str]:
    top = STAGES.index(max_stage)
    path = ["FRIENDSHIP"]
    for s in STAGES[1 : top + 1]:
        if s == max_stage or s in ("RISK_ASSESSMENT",) or rng.random() < 0.7:
            path.append(s)
    return path


def gen_grooming(
    rng: random.Random, g: Gen, clock: Clock, child: str, other: str, max_stage: str, speed: str
) -> list[dict]:
    msgs: list[dict] = []
    per_stage = (1, 2) if speed == "fast" else (2, 4)
    for _ in range(rng.randint(2, 6)):  # opening chatter
        if rng.random() < 0.5:
            _msg(msgs, other, False, g.pick(FILLER_OTHER), clock.tick())
        else:
            _msg(msgs, child, True, g.pick(FILLER_CHILD), clock.tick())
    for stage in stage_path(rng, max_stage):
        if speed == "slow":
            clock.gap(4, 40)
        for _ in range(rng.randint(*per_stage)):
            _msg(msgs, other, False, g.pick(STAGE_TEMPLATES[stage]), clock.tick(), stage)
            if rng.random() < 0.8:
                _msg(msgs, child, True, g.pick(CHILD_REPLIES[stage]), clock.tick())
            n_filler = rng.randint(0, 2 if speed == "fast" else 4)
            for _ in range(n_filler):
                if rng.random() < 0.5:
                    _msg(msgs, other, False, g.pick(FILLER_OTHER), clock.tick())
                else:
                    _msg(msgs, child, True, g.pick(FILLER_CHILD), clock.tick())
    if rng.random() < 0.5:  # trailing chatter
        for _ in range(rng.randint(1, 3)):
            _msg(msgs, child, True, g.pick(FILLER_CHILD), clock.tick())
    return msgs


GROOMING_DEPTHS = (
    ["FRIENDSHIP"] * 11
    + ["RISK_ASSESSMENT"] * 15
    + ["TRUST_EXCLUSIVITY"] * 7
    + ["ISOLATION"] * 7
    + ["SECRECY"] * 13
    + ["PLATFORM_MOVE"] * 19
    + ["ESCALATION"] * 28
)


def assign_splits(n: int, rng: random.Random) -> list[str]:
    n_train, n_val = round(n * 0.70), round(n * 0.15)
    splits = ["train"] * n_train + ["val"] * n_val + ["test"] * (n - n_train - n_val)
    rng.shuffle(splits)
    return splits


def generate(n: int = 600, seed: int = 1337) -> list[dict]:
    master = random.Random(seed)
    n_benign = round(n * 0.50)
    n_hn = round(n * 0.15)
    n_groom = n - n_benign - n_hn
    kinds = ["benign"] * n_benign + ["hard_negative"] * n_hn + ["grooming"] * n_groom
    # Stratified 70/15/15 split by conversation within each label.
    kind_offsets = {"benign": 1, "hard_negative": 2, "grooming": 3}  # str hash() is not stable
    splits_by_kind = {
        k: assign_splits(kinds.count(k), random.Random(seed * 10 + off)) for k, off in kind_offsets.items()
    }
    counters = Counter()
    convs: list[dict] = []
    base = datetime(2025, 1, 6, 15, 0, 0)
    for i, kind in enumerate(kinds):
        rng = random.Random(master.randint(0, 2**31))
        split = splits_by_kind[kind][counters[kind]]
        counters[kind] += 1
        platform = rng.choice(PLATFORMS)
        g = Gen(rng, platform, split)
        clock = Clock(rng, base + timedelta(days=rng.randint(0, 300), minutes=rng.randint(0, 600)))
        child, other = handle(rng), handle(rng)
        speed, max_stage = "n/a", "NONE"
        if kind == "benign":
            cat = BENIGN_CATS[i % len(BENIGN_CATS)]
            if cat == "parent_checkin":
                platform, other = "Messenger", "Mom"
            msgs = gen_pooled(rng, g, clock, BENIGN[cat], child, other, rng.randint(12, 40))
        elif kind == "hard_negative":
            cat = HN_CATS[i % len(HN_CATS)]
            if cat == "coach_teacher":
                other = rng.choice(["Coach Dan", "Ms. Reyes", "Coach Mia", "Mr. Santos"])
            msgs = gen_pooled(rng, g, clock, HARD_NEGATIVE[cat], child, other, rng.randint(12, 36))
        else:
            max_stage = GROOMING_DEPTHS[(counters["grooming"] - 1) * len(GROOMING_DEPTHS) // n_groom]
            speed = "fast" if rng.random() < 0.5 else "slow"
            cat = f"grooming_{max_stage.lower()}_{speed}"
            msgs = gen_grooming(rng, g, clock, child, other, max_stage, speed)
        convs.append(
            {
                "id": f"syn-{i:05d}",
                "platform": platform,
                "label": kind,
                "category": cat,
                "max_stage": max_stage,
                "speed": speed,
                "split": split,
                "child": child,
                "messages": msgs,
            }
        )
    return convs


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--n", type=int, default=600)
    ap.add_argument("--seed", type=int, default=1337)
    ap.add_argument("--out", type=Path, default=ROOT / "data" / "synthetic")
    args = ap.parse_args()
    if args.n < 300:
        print(f"note: --n {args.n} is below the recommended 300")
    convs = generate(args.n, args.seed)
    args.out.mkdir(parents=True, exist_ok=True)
    for split in ("train", "val", "test"):
        with (args.out / f"{split}.jsonl").open("w", encoding="utf-8", newline="\n") as f:
            for c in convs:
                if c["split"] == split:
                    f.write(json.dumps(c, ensure_ascii=False) + "\n")
    manifest = {
        "seed": args.seed,
        "n": len(convs),
        "by_label": Counter(c["label"] for c in convs),
        "by_split": Counter(c["split"] for c in convs),
        "by_split_label": Counter(f"{c['split']}/{c['label']}" for c in convs),
        "grooming_depth": Counter(c["max_stage"] for c in convs if c["label"] == "grooming"),
        "messages": sum(len(c["messages"]) for c in convs),
    }
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
