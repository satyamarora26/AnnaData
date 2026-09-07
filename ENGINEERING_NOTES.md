# AnnaData — engineering notes

A record of what was built, what went wrong, and why the system is shaped the
way it is. Written as the work happened, so the reasoning survives the code.

---

## 1. What the system is

An agricultural advisory service for Indian farmers, reachable two ways:

- **SMS**, so a farmer with a basic phone and no internet can use it
- **Web**, for those who have a smartphone

Both channels call the same agent. The SMS side runs through an Android handset
acting as a gateway, which is the decision the whole architecture rests on.

```
Farmer (SMS) ──▶ Android phone ──webhook──▶ SMS bridge ──▶ Agent API ──▶ Gemini
                  (sms-gate.app)             (Quart)        (FastAPI)      │
                                                                ├── soil (Earth Engine)
Farmer (web) ──────────────────────────────▶ React app ────────▶├── weather (Open-Meteo → MET Norway)
                                                                ├── prices (data.gov.in)
                                                                ├── doses (CIB&RC table)
                                                                └── memory (Postgres)
```

---

## 2. Decisions that shaped everything

### An Android phone as the SMS gateway, not Twilio

India's A2P/DLT regime requires a registered sender ID and **pre-approved
message templates**. Free-form AI answers cannot be templated, so the
conventional route is closed at any volume. A handset sending ordinary
person-to-person SMS sidesteps it entirely, costs nothing beyond the SIM's
plan, and works today.

The trade is honest: one phone is a single point of failure, and it will not
scale. It is the right call at prototype stage and the wrong one at scale.

### Cloud mode over local server

The original setup needed the phone and server on the same Wi-Fi plus an ngrok
tunnel whose URL rotated on every restart and had to be re-registered by hand.
Cloud mode gives a fixed endpoint and lets the phone work from anywhere on
mobile data. This removed the deck's own "not deployed beyond ngrok"
limitation.

### Every integration optional, none fatal

A missing key disables one tool and nothing else. The service starts with only
a Gemini key, reports what is configured at `GET /health`, and answers around
whatever is absent. Before this, a missing Earth Engine key raised at import
and took the entire API down.

### Postgres + pgvector instead of AWS Bedrock

The original design put the knowledge base on Bedrock. Bedrock knowledge bases
need a vector store, and the default — OpenSearch Serverless Classic — bills a
minimum of roughly **$345–700/month** whatever it holds. The Neon Postgres
already provisioned for farmer profiles has pgvector, and the Gemini key
already has an embeddings model. Same capability, no new account, no cost.

### Structured lookup for doses, vectors for prose

These are different problems. A pesticide dose is a precise fact with a right
answer; retrieving prose that mentions a chemical does not tell you the
approved rate for a given crop and pest. Doses live in a table keyed on
(crop, pest). Open-ended material — schemes, storage, practice — goes in
pgvector, where similarity is the right tool.

The table's most important property is what it **refuses**. With nothing on
file it instructs that no product and no dose be named at all.

### Compute facts in code; do not ask the model to infer them

This pattern recurred three times and worked every time:

| Problem | Prompting alone | Computed in code |
|---|---|---|
| Sowing windows in the past | kept suggesting June in August | inject today's date + cropping season |
| Hinglish answered in Devanagari | "match their language" ignored | detect script, state it outright |
| Tool selection | — | intent → tool map, no model call |

When a fact is knowable deterministically, knowing it beats asking.

---

## 3. Problems, and what they turned out to be

### The SMS channel never worked — two independent bugs

1. The bridge read `data["response"]`; the backend returns `{"answer": ...}`.
   Every reply was discarded.
2. The bridge read `payload["phoneNumber"]`; the gateway sends the originating
   number as `sender`. Even a working reply was addressed to `None`.

Neither would have been found without reading the gateway's own documentation.

### Devanagari crashed the request

Windows consoles default to cp1252, so `print()` of Hindi text raised
`UnicodeEncodeError` and killed the request before the agent was called. A
farmer texting in Devanagari — most of them — got nothing. Fixed by forcing
UTF-8 on stdout at import.

### Models silently retired

`gemini-2.0-flash` and the 2.5 line are no longer callable by new API keys; the
API returns 404 pointing at the 3.x line. Worse, `models.list` **still lists
models the key cannot invoke**, so the listing cannot be trusted — only a real
`generateContent` call settles it.

### A 60-second stall that was not a timeout

`langchain_google_genai` reads `max_retries` from *call* kwargs, not the
constructor. Setting it on the model silently does nothing, and the default is
six attempts with exponential backoff — about 60s spent on a dead model before
the fallback is tried. Bound as a call kwarg, the same failover takes ~1s.

### 93% of latency was one dead API

Profiling a rice-disease question: 97s total, of which 90s was fetching mandi
prices the answer would never mention. Every tool ran whenever coordinates were
known, sequentially. Selecting tools by intent and running them concurrently
took it to 4s — and the answer improved, because the relevant reading was no
longer buried under hundreds of irrelevant price rows.

### Soil organic carbon reported at double its value

OpenLandMap stores it divided by 5; the code reported the raw pixel as a
percentage. A soil at a genuine 0.50% — low, needs compost — printed as 1.00%,
which reads as adequate.

### The soil texture table was inverted

OpenLandMap codes **1 as Clay and 12 as Sand**. The lookup had it backwards, so
every reading came back as roughly the opposite soil. Nagpur — black cotton
clay — was reported as sand. Irrigation and drainage advice follows directly
from texture, so the reversal inverts everything built on it. This bug predated
the current work and would have shipped the moment soil went live.

### Parsing the pesticide register: misattribution, not data loss

Product headings were matched against a fixed list of formulation codes, which
missed thirty in forty pages. A missed heading does not drop rows — it
attributes them to the **previous product**. Wrong chemical, convincing dose.
Detection now keys on the strength every product carries, and an unrecognised
lone cell **clears** the current product so its rows are dropped rather than
misfiled. That cost 256 rows out of 2,722, which is the right trade.

Separately, rows missing an active-ingredient figure shift their columns left
and land a dilution volume in the waiting period, producing pre-harvest
intervals of "500–1000" days. Anything beyond a season is now discarded: no
waiting period is honest where a wrong one is not.

### Retrieval answered questions the corpus did not cover

With the similarity floor at 0.45, a question about solar pump subsidies
returned PM-KISAN passages — loosely related, since both are agricultural
schemes — and the model, handed something rather than nothing, answered from
general knowledge anyway. Retrieval had turned a guess into a *more confident*
guess, which is worse than no retrieval at all.

Measuring the corpus settled the threshold rather than intuition:

| Question | Top similarity |
|---|---|
| how much does PM-KISAN pay | 0.739 |
| who is excluded from PM-KISAN | 0.801 |
| which scheme gives me a solar pump | 0.578 |
| crop insurance for hailstorm damage | 0.536 |
| how do I build a cold storage | 0.475 |
| what is the price of cotton today | 0.469 |

Covered questions score 0.68–0.80; uncovered ones top out at 0.578.

The floor was set at 0.65, and **adding four more documents broke it**. A
question about tractor subsidies then scored **0.667** against a PMFBY passage
about Gujarat leaving the scheme — close on wording, entirely wrong on
substance — and the model answered by naming "PM Kisan Tractor Yojana", a
scheme widely circulated online that does not exist as a central scheme.

A single similarity number cannot separate relevance from adjacency, and the
problem grows with the corpus: more documents mean more chances that something
irrelevant clears the bar. The floor is now **0.70**, and the prompt also
requires the model to check that a retrieved passage actually addresses the
question before using it, rather than stretching one about a different scheme
to fit. This threshold is corpus-dependent and will need revisiting again.

### Not all sources deserve equal weight

The corpus mixes government documents with Wikipedia articles. Both are useful;
only one is authoritative. A farmer acting on an insurance deadline or a
subsidy amount should know which they were told, so documents carry a tier —
`official` or `reference` — and the model is shown it. Where only unofficial
material matches, the answer says the details are indicative and to confirm
with the agriculture office before acting on a date, amount or eligibility
rule.

### PDF extraction emits bytes Postgres will not store

Four chunks of a nine-page factsheet vanished with
`PostgreSQL text fields cannot contain NUL (0x00) bytes`. Extraction leaves NUL
and other control characters in the text; they are now stripped during
cleaning.

### A 71-byte response rejected as "output too large"

Every keep-warm ping failed with that message, and after enough failures the
scheduler disabled all three jobs - so the services then slept, and the pings
that remained hit cold starts and returned 503. A self-sustaining outage from a
monitor that was supposed to prevent one.

The responses were 71 and 247 bytes. The cause was in the headers:

    Transfer-Encoding: chunked

Render serves everything chunked with no `Content-Length`, so a client cannot
know the size before reading it, and a monitor that refuses to read an unbounded
response reports even a tiny one as too large. Nothing about the payload was
wrong.

`/health` and `/` now answer HEAD as well as GET. A HEAD response has no body,
so there is nothing to bound and the check cannot trip.

### A bank alert became a farmer

The first live run of the rating scheduler reported one farmer due and none
asked. The farmer was `JR-JIOPAY-S`.

That is a DLT sender ID - the alphanumeric identifier banks, delivery services
and marketers send from. An Indian handset receives far more of this machine
traffic than conversation, and one of them had been treated as a farmer: it was
answered by the agent at the cost of a model call, written to the farmer table,
assigned a rating day, and then came up due on every scheduler run, because the
ask was recorded only when the send succeeded and a send to an alphanumeric
sender ID can never succeed. A quarter-hourly loop that could not terminate.

The test that matters is whether a reply could ever arrive, so a sender that can
be answered is a phone number and anything containing a letter is a machine.
The check now runs at ingest, before a model call or a database row - and again
in the due-check, for rows already stored.

### Ninety seconds to say "unavailable"

With data.gov.in down, a market-price question took 90.4s to return "Mandi price
data unavailable" - two attempts at a 45-second timeout, on SMS, where nothing
tells the farmer the question was even received.

A generous timeout is worth having when an upstream is slow. This one is not
slow: it answers in a second or two when it is up and not at all when it is
down, so the extra thirty seconds bought nothing and were paid every time. The
per-request timeout is now 12s with one attempt, the paged fetch has an overall
20s deadline (paging multiplied the timeout by the page count), and the circuit
opens after two failures rather than three.

| Market-price question, upstream down | Before | After |
|---|---|---|
| First question | 90.4s | 12.2s |
| Once the circuit is open | 90.4s | 0.0s |

### Support prices, because they are the part that keeps

Agmarknet was re-probed for a live price feed and is now a React application:
the old .aspx report pages return a 1 KB shell, and the API behind it
(`api.agmarknet.gov.in/v1/`) answers `commodities` and `agmarknet-live-date`
without credentials but gates every price endpoint behind
`TOKEN_OR_CAPTCHA_REQUIRED`. Their robots.txt allows crawling and disallows only
auth, data-entry and admin paths, so reading is permitted - but a captcha is not
something to work around, so there is still no automated daily feed.

What the site's CSV export does carry is worth more than the day's prices. Its
"Marketwise" report has no market column - it is a national daily summary - but
it includes the Minimum Support Price for twenty commodities. An MSP is set once
a marketing year by the Cabinet Committee on Economic Affairs, applies
nationally, and does not move, which makes it the rare agricultural figure that
is still correct months after it is stored.

So it is stored. A price question now answers with the guaranteed floor even
while the live service is down, which turns the most common failure from
"unavailable" into a real answer:

> Current mandi price data for Maharashtra is unavailable. However, the
> government guaranteed Minimum Support Price for cotton for the 2026-27 season
> is Rs 7,710 per quintal. You should not sell your crop below this floor price
> at procurement centers.

The commodity labels are unpacked into the vocabulary farmers actually use -
`Red gram/Arhar/Tur(whole)` becomes arhar, tur, red gram and pigeonpea,
`Sesamum(Sesame,Gingelly,Til)` becomes til - giving 39 aliases across 20
commodities, so a question in Hindi finds the figure.

The safety case is the crop with no MSP. Onion, potato and tomato have none, and
a model that has seen the phrase in twenty other answers will readily produce a
plausible one. Asked for the tomato rate it now says no MSP exists for tomato
rather than inventing a number, and an eval case holds it there.

### Three failures in one SMS thread

A farmer asked about sugarcane and the thread fell apart in three separate ways,
each with a different cause.

**"yess" was answered with "Namaste! Greetings from Nagpur."** The rewriter had
done its job - it turned "yess" into "Yes, I would like to check the government
support price for sugarcane" - but `message_type` is classified from the raw
message while `intent` is read from the rewrite. They disagreed, `smalltalk`
against `scheme_subsidy`, and the message type decides the routing, so the
farmer accepting an offer was greeted from scratch. Smalltalk carrying a topical
intent is a contradiction; it is now treated as a continuation, the same way a
statement carrying one is treated as a question.

**It asked for the crop stage twice, the second time right after being told the
crop was harvested.** The advisory prompt had never been given the conversation
- only the current question - so it could not know what it had just asked or
what had been answered. It now receives the last six turns, with instructions
not to repeat a question already asked and to treat what the farmer has said as
known.

**Then it invented a price.** Asked for the sugarcane MSP with no figure on
file, it produced "the Fair and Remunerative Price for the 2026-27 season is 340
rupees per quintal" - fluent, specific, and made up - which carried into the
next turn as established fact. Sugarcane is covered by FRP rather than MSP, and
we hold no FRP data at all.

The prompt already forbade this. That is the point: an instruction not to state
a figure competes with a fluent continuation and loses often enough to matter,
exactly as it did with pesticide doses. The check is now code. If no support
price was retrieved for the crop, a sentence naming a support scheme and a
number does not leave the building - and since telling a farmer to ask the sugar
mill is genuinely useful, only sentences asserting both a scheme and a figure
are dropped.

The cost of getting this wrong is not a clumsy sentence. It is a farmer selling
a harvest against a price nobody guaranteed.

### A health check that could not see the outage

Inbound messages stopped for half an hour. Both services reported `status: ok`
throughout, the webhook route answered correctly when probed directly, and the
farmer's number passed every filter. Nothing anywhere said a word.

The gateway had been holding the messages and retrying: when the service was
restarted by hand, three arrived in the same second. So the outage was inside
the bridge, and the diagnosis that mattered was not which component had failed
but why nothing had noticed.

Every outbound call - to the gateway and to the agent backend - goes through one
`ClientSession` created at startup. If that session dies, the process keeps
accepting webhooks and failing every one of them, and `/health`, which only read
configuration, cannot tell: the configuration is still perfectly valid. It was
answering a question nobody was asking.

Two changes. The session is now fetched through a helper that reopens it if it
has been closed, so this particular wedge resolves itself instead of waiting to
be noticed. And `/health` reports what a reply actually depends on - whether the
session is open, whether the backend answers - so the monitor already pinging it
every fifteen minutes can see a failure rather than certify a healthy config
while nothing works.

The general form: a health check that reports only what the process knows about
itself will pass for exactly as long as it takes someone to notice the silence.

### One broken cron job, and farmers lose their messages

Two complaints arrived together - the scheduler was failing again, and messages
were not going through. They were the same fault.

The scheduler had been rejecting the keep-warm pings, so nothing kept the
backend awake. A farmer wrote after a quiet spell, the bridge called a sleeping
backend, and the message was dropped. The bridge already carried a 150-second
timeout and a retry for exactly this case, and neither helped: free hosting does
not hold the connection while it wakes, it refuses immediately, so both attempts
landed inside the same cold start and the whole thing failed in under a second.
A timeout cannot save you from a service that answers quickly with a refusal.

Retries now wait, and the wait grows - attempts fall at roughly 0, 20, 60 and
120 seconds, against a cold start of about 140 - so the last one arrives after
the backend is up rather than during.

The scheduler failure had the same shape as before. Render sends every response
chunked with no `Content-Length`, and a scheduler that cannot bound a response
rejects even a twenty-byte one. `/health` answers HEAD, which has no body; the
feedback task cannot, because it has to run, so it now returns `204 No Content`
and logs its result instead. The numbers are on `/feedback/summary`.

One more thing surfaced: the health check added the day before reported
`backend_reachable: false` beside `status: "ok"`, because a problem was recorded
only when the request raised, not when it returned a refusal. It had found the
outage and then called itself healthy.

The chain is worth keeping in view. A monitoring job that could not read a
seventy-byte response ended with farmers' questions being silently discarded.

### Asked to rate, rated, asked again a minute later

A farmer was asked for a rating, replied 5 at 12:01, and was asked again at
12:02. The timestamps name the cause exactly: `feedback_asked_at` read 12:02:26
while the rating was recorded at 12:01:19 - the ask came after the answer.

One column was carrying two facts. `feedback_asked_at` recorded both "a reply is
pending, so read the next message as a rating" and "this is when the farmer was
last asked, so do not ask again this month". Recording a rating cleared the flag
to stop the following message being parsed as a score, and in doing so erased
the only record that the farmer had been asked at all. The next scheduler run
found a farmer with no ask on file and asked again.

The two facts are now separate. The ask is never cleared, and whether a reply is
pending is answered by comparing it against the ratings: an ask older than the
most recent rating has been answered. The due-check also excludes anyone with a
rating inside the cooldown, so a farmer who rates unprompted still counts as
heard from.

This one could not be written as an eval case - there is no language in it, only
scheduling - so it is a database test instead, `eval/test_feedback.py`, which
walks a synthetic farmer through ask, rate and cooldown. Restoring the original
behaviour makes it fail on exactly the assertion that matters, which is the only
evidence that a regression test is worth having.

### A second way in, after the first one broke

The gateway handset stopped enumerating over USB - `VID_0000`, code 43, no
descriptor at all - and with a failing screen it could not be driven any other
way. The service was unreachable, and the single point of failure the notes had
been describing since the start stopped being hypothetical.

WhatsApp Cloud API is now a second channel. It is worth having for three
reasons beyond redundancy. Meta hosts the connection, so nothing depends on a
device staying awake. Free-form replies are permitted inside the twenty-four
hour window a farmer's own message opens, which is exactly the regulatory
problem the SMS path cannot solve at scale. And a test number is issued before
any business verification, so the channel runs without a SIM.

It reaches a different farmer - one with a smartphone - so it widens the
audience rather than replacing the SMS path, which remains the one that reaches
a feature phone.

The farmer is the same person on either channel, so WhatsApp's bare
`917388535376` is normalised to the `+917388535376` the profile store already
keys on. Someone who asks over SMS and later asks on WhatsApp keeps their
district, their crops and their history. Both channels run the same pipeline -
opt-out, then a possible rating, then the agent - because a farmer switching
channels should not meet a different assistant.

Two things differ, and both are in the channel rather than the agent. The
segment budget is an SMS constraint, so trimming is skipped where there are no
segments. And Meta delivers delivery receipts and read receipts through the same
webhook as messages, so anything that is not an inbound message is acknowledged
and ignored.

### The keep-warm problem was arithmetic, not configuration

Three times the scheduler was fixed and three times the services went back to
sleep, so the fourth attempt started with the platform's own numbers rather than
with the scheduler. A free web service sleeps after 15 minutes idle, takes about
a minute to wake, and the whole workspace has **750 instance-hours a month**.

One service awake around the clock is 730 hours. Two is 1,460.

So keeping both warm was never a configuration problem to solve - it needs
almost twice the entire monthly allowance, and pinging hard enough to try would
exhaust it in a fortnight and suspend every service until the next month. Every
fix so far had been aimed at the wrong layer.

It also explains the scheduler's behaviour exactly. The ping interval was 15
minutes against a 15-minute idle timer, which is a race; the request timeout was
30 seconds against a 60-second wake. So a ping landing just after a spin-down
always failed, and enough failures disabled the job, after which nothing kept
anything warm at all.

The answer is a platform that starts in seconds instead of a minute, which makes
keeping anything warm unnecessary. Cloud Run scales to zero the same way and
needs no scheduler.

Moving revealed two things worth fixing regardless. Both Dockerfiles carried a
literal `
` where a line continuation was meant - invisible on Render, which
builds from a start command rather than the Dockerfile, and fatal to a container
build. And the image was installing PDF parsing, YAML and the AWS SDK, none of
which the serving path reaches: the ingestion scripts and the evaluation harness
use them, and the Bedrock fallback is now imported lazily. They moved to
`requirements-tools.txt`, because paying for them in every cold start to support
code that never runs is the wrong trade.

### The third connection that reported healthy while doing nothing

After the move to Cloud Run, replies stopped arriving. The logs made the shape
of it obvious in a way Render never could:

    09:59:56  Received SMS: "are you active now?"
    10:00:10  AI Reply: "Yes, I am active and ready to help..."
    10:00:13  SMS sent to +917388535376 (209 chars, 2 segments)

Everything on our side worked. The gateway's own record showed why nothing
arrived: five replies had gone `Pending -> Processed -> Sent -> Delivered`, and
the sixth sat at `Pending` and never moved. The message had been queued for a
handset that was not collecting it.

The handset looked fine. The app was running with a foreground service, doze
reported `ACTIVE`, and its own screen said `Internet connection: available` with
the cloud server connected. Stopping and starting the service cleared the queue
within four seconds. The connection had died without anyone noticing, including
the app holding it.

That is the third time this project has been bitten by the same thing. The SMS
bridge kept accepting webhooks through a dead HTTP session while `/health`
reported `ok`. The health check that replaced it reported `backend_reachable:
false` beside `status: ok`, because a problem was only recorded when a request
raised rather than when it was refused. Now a gateway app showed a live
connection it was not receiving on.

The fix was already in the app, switched off: a **Ping interval**, labelled
"online status at the cost of battery life", set to `Not set`. A connection
nobody exercises is a connection nobody can tell is dead, and on a device that
exists only to relay messages, battery is the cheaper side of that trade.

Setting it to 60 seconds fixed the stall and created a latency problem, because
push was evidently not reaching this handset at all: the app was collecting
messages only on its ping cycle, so every reply waited an average of half the
interval. The gateway's own timestamps separate the outbound leg cleanly, and
the interval is visible in them:

| Period | Pickup | Send | Deliver | Total |
|---|---|---|---|---|
| Push working | 2.0-4.3s | 0.4s | 1-3s | 3.6-7.0s |
| Connection stale | 19.8-254.6s | 0.5s | 2-4s | 22.6-258.4s |
| Ping at 60s | 50.6-80.9s | 0.5s | 3-4s | 56.4-84.6s |
| **Ping at 10s** | **5.8s** | **0.4s** | **4.5s** | **10.7s** |

Sending and delivery were never the problem - they are consistently under five
seconds. The whole variance is in the handset noticing there is something to
send. At 10 seconds the outbound leg is back to its healthy baseline, and the
tablet stays on mains power, where Android throttles background networking far
less aggressively.

End to end a farmer now waits roughly 20 seconds: two to eight for the inbound
SMS, nine to sixteen for the agent, and about ten to get the reply back out. The
agent is now the largest share, which is the first time in this project that the
slow part has been our own code rather than something waiting on a network.

### Two optimisations, and the measurement that redirected both

With the delivery path fixed, the agent became the largest share of what a
farmer waits for, so it was worth measuring properly rather than guessing. A
single sample suggested refinement cost 4.19s of a 9.84s answer - 43%, an
obvious target. Four runs said otherwise:

| Call | Median |
|---|---|
| refine | 3.32s |
| extract | 3.82s |
| advise | 3.49s |

The three calls cost about the same. There is no dominant one to tune, and the
only way to make the agent meaningfully faster is to make one of them not
happen. Trimming the conversation passed to refinement was tried first and did
nothing - 10 turns took 2.17s against 1.81s for 2, which is noise.

Refinement is the one that can sometimes be skipped, because it exists solely to
resolve a message against what came before, and a question naming its own
subject has nothing to resolve. The skip is deliberately timid: it happens only
on positive evidence that the message stands alone - it names a crop the system
knows, and it is at least five words. Everything short, ambiguous or unfamiliar
is refined exactly as before, because getting this wrong is precisely how "yess"
was once answered with a greeting.

**The Indic segment penalty was a compliance problem, not a physics one.** A
Devanagari answer went out as four segments for 204 characters. The prompt
already said to keep non-Latin replies under 30 words; measured, the model was
writing 45 and 36, because counting its own output is not something it does
reliably.

So the budget is computed in code and given as a number, the same reason the
script is decided in code rather than described: 67 characters a segment for
UCS-2, 153 for GSM-7, three segments, stated as a hard character ceiling.

| Answer | Before | After |
|---|---|---|
| Bollworm, Devanagari | 230 chars, **4 segments** | 195 chars, **3 segments** |
| Sowing, Devanagari | 168 chars, 3 segments | 175 chars, 3 segments |
| Bollworm, English | - | 260 chars, 2 segments |

A quarter off the delivery cost of the worst case, with the registered dose and
the pre-harvest interval both still in the answer. The English budget is 459
characters against Devanagari's 201, which is the honest shape of the
constraint: the same advice costs more than twice as much to deliver in the
farmer's own script.

### Reading a day of real conversation

Thirty-four messages from one farmer in one morning, read end to end, showed
three faults that no eval case had caught because none of them had been thought
of.

**A refusal lost the thread.** Asked whether he kept animals whose dung he could
use, the farmer answered "nahin" and was greeted from scratch - then again eight
minutes later, after he asked "you forgot what we were talking about?". The
earlier fix caught affirmations, because "yess" rewritten against the
conversation carries a topic and smalltalk carrying a topic is a contradiction.
A refusal carries nothing. One word, no subject, no clue.

So the signal cannot be the message; it has to be what preceded it. Every reply
ends with a question where one would help, so a short message following one of
our questions is an answer to it, whatever the classifier reads out of a single
word.

**The answer was a dead end even when it kept the thread.** Told the farmer had
no animals, the assistant had nothing further to offer. A farmer who cannot do
what was suggested needs the next option that fits what they do have - dung
bought from a neighbour, poultry manure, green manure grown in place - and the
prompt now says so. Refusing without offering is the unhelpful half of honesty,
and it applies to the farmer's constraints as much as to our own gaps.

**The profile had been accumulating things that are not crops.** The stored list
read `watermelon, small plant, flower, indoor potted plant, indoor potted
plants, wheat, potato` - the last two of the junk entries the same non-crop
recorded twice. The greeting offered help with "your watermelons and plants",
and an acknowledgement described five acres of indoor potted plants.

Whether a name is a crop is a question the dose register can answer, since it
lists 314 of them. A name is now stored only if it resolves against that
register or the synonym table, 352 names in all, with plurals folded so
"tomatoes" becomes tomato while "indoor potted plants" becomes nothing. The
polluted profile was cleaned to `watermelon, wheat, potato`.

One more thing came out of reading the log rather than the code: the assistant
recited its full capability list to a farmer on his third greeting of the
morning. A greeting to someone already known is a resumption, not an
introduction, and it now reads like one.

### The refusals were a vocabulary gap, not a coverage gap

The dose table refuses when it holds no registered use for a crop and pest, and
the obvious worry is that farmers grow anything and everything: at scale, does a
register of 2,456 uses just refuse constantly?

Measuring it first gave a different answer. Against realistic English pest names
the register hit 16 of 18 - the top crops are well covered, with 199 rows for
cotton, 165 for chilli, 132 for tomato - and one of the two misses was correct,
since tomato leaf curl is a virus and not something a pesticide register treats.

Against the words farmers actually use it hit **1 of 14**.

    kapas me sundi   ->  no approved use
    (eight rows sat in the table under "Bollworm")

The register is written in English and scientific names; farmers write "sundi",
"safed makkhi", "gerua", "tela". The docstring on the matching function had
claimed for months that it was loose enough to handle exactly this - "farmers say
sundi and bollworm and इल्ली for the same insect" - and nothing implemented it.
Matching was plain substring, so the two vocabularies could never meet.

A synonym table closes it, mapping each vernacular term to several register
fragments rather than one name, because the vernacular is broader than the
register's vocabulary and the crop already narrows it. "sundi" is any caterpillar
or borer: in cotton that reaches bollworm, in rice stem borer, in gram pod borer.
Letting the crop disambiguate is more honest than pretending one Hindi word names
one species. Thirteen of thirteen now match.

Generic words are deliberately excluded. "keeda" means insect; resolving it to a
particular pest would be guessing at which chemical to recommend, so it matches
nothing and the answer asks what the farmer is actually seeing.

The refusal itself is unchanged and was re-checked: elephant damage, hailstorm,
drought and cow trampling all still return nothing, and a cotton query still
returns only cotton rows.

Worth stating for anyone extending this: the coverage question was worth
measuring before answering. The instinct was to go and find more data. The
measurement said the data was already there and could not be reached.

### Why the dose table will not be extended by the model

The obvious way to widen coverage is to let the model answer when the register
cannot: give the dose it recalls, attach a caution, let the farmer verify. It
was considered and rejected, for reasons this project has already paid to learn.

The model fabricates confidently in exactly this shape. Asked for a support
price it did not hold, it produced "the Fair and Remunerative Price for the
2026-27 season is 340 rupees per quintal" - fluent, specific, invented, and
produced *despite an explicit instruction in the same prompt forbidding it*.
`price_guard.py` exists because that instruction failed. Extending the same
mechanism to pesticide doses would reverse the work.

The second reason is stronger and particular to this data. The register does not
record what is *known*; it records what is **registered**, and registration is
crop-and-pest specific. A recommendation outside it is a recommendation to apply
a pesticide off-label. That is a different category of wrong from inaccurate, and
a caution does not address it - the farmer receives a chemical and a quantity,
and the quantity is what gets sprayed. "Please verify" is read once; so is the
label, and the label is what governs.

The consequences are asymmetric in a way that settles it. A wrong scheme name
wastes a trip to an office. A wrong dose damages the crop, exposes whoever
sprays it, can leave residues that fail procurement, and drives resistance.

**The register is already fully loaded.** All six lists CIB&RC publishes -
insecticides, fungicides, herbicides, bio-insecticides, bio-fungicides and plant
growth regulators - are in the table, 2,456 uses. There is no seventh list. And
248 of 331 crops carry only one or two approved uses, which is not a gap in our
collection but the regulatory reality: that is what has been registered.

So coverage grows by adding *authoritative* sources, not generated ones: state
agricultural university Packages of Practices, which are authoritative for their
state and cover local crops the central register handles thinly; ICAR
crop-institute recommendations; and NCIPM's integrated pest management packages.
All are documents, and the ingestion path for documents already exists.

### What a refusal owes the farmer

Refusal being common and permanent makes its quality a first-order concern
rather than an edge case. The thing that must not be generated is a chemical and
a quantity. Everything else is fair, useful and carries no chemical risk:
destroying affected material, field sanitation, pheromone and bait traps,
bagging fruit, burying fallen fruit, rotation, resistant varieties, timing,
hand-picking. These are frequently what the extension officer advises first.

The refusal was inconsistent about this - offering pruning advice for one crop
and nothing at all for another - and is now required to carry at least one
measure that needs no registered product.

Adding that instruction immediately broke something else, which is the
recurring lesson arriving again. "Do not stop at the refusal" was written
without scope, and an unrelated safety case - a scheme question whose answer
must contain no invented subsidy figure - began failing **two runs in four**.
The instruction had bled across from pest control to schemes. Scoping it
explicitly restored four passes in four.

That fix rested on wording, which is precisely what this project had twice found
insufficient for numbers, so it did not stay there. `price_guard.py` became
`output_guards.py` and now checks both claims with the same machinery: a
sentence asserting a scheme **and** a figure is removed when retrieval reported
it covered nothing, exactly as a sentence asserting a support price and a figure
is removed when no support price was retrieved.

The distinction that makes it usable is what it leaves alone. Naming a scheme
without a figure survives, because telling a farmer which office to visit is
useful and true. A helpline number survives, because a phone number is not a
claim about money. And a figure that retrieval *did* support survives, because a
farmer who asks what PM-KISAN pays, and whose question the corpus covers, should
be told.

The case that had failed two runs in four now passes five in five.

### The harness outruns the quota

Running the full suite repeatedly produced erratic results - 33 passed, then 32
with one failure, then 28 with two failures and four errors - while the model
itself answered five consecutive probes in 1.6s each. The suite fires roughly a
hundred requests back to back, which exceeds the free tier's rate limit, and the
failures were the harness starving rather than the system regressing.

A suite that cannot be trusted on a single run is worth knowing about before a
result from it is believed. Cases must be confirmed individually or by tag, with
spacing, until the quota question is settled.

### Not offering what is not working

data.gov.in has now been failing continuously since 29 August. The diagnosis is
unambiguous and is not ours: the portal answers in 1.7s while the API host
accepts the TCP connection and then sends nothing at all until the timeout
expires. Not a 500, not a 404 - silence.

Meanwhile `/health` reported `mandi_prices: true` and, asked what it could help
with, the assistant offered daily market prices. Both were answering a question
about *configuration* - a key is set - rather than about reality.

That is the same fault as the one fixed months earlier in the opposite
direction, when the assistant denied holding scheme documents while the store
held eighty-six passages about them. Announcing a capability that is failing
misleads a farmer exactly as much as denying one that works; only the direction
differs.

The capability is now withdrawn while the upstream is down, and returns on the
first success. Two details made it work properly:

The circuit breaker lives in one process, and on a platform that scales to zero
most processes are new - so a fresh instance believed the upstream was healthy
until it failed once, and advertised prices in the meantime. The outcome of each
call is now recorded in the database, so the outage survives a restart and is
shared across instances.

And the withdrawal is narrow. Support prices are still offered, because the MSP
table is local and still correct; only the live feed is gone. A farmer asking
about prices still gets the guaranteed floor, which is the whole reason that
table exists.

### There is no second source for mandi prices

data.gov.in has returned 502 for days, and the obvious replacements do not
exist in machine-readable form. Agmarknet - which is where data.gov.in gets the
figures - serves a one-kilobyte JavaScript shell; eNAM's trade endpoint returns
its own homepage to a POST; CEDA is a dashboard. The government API *is* the
readable Agmarknet feed, so there is nothing to switch to.

What can be done is degrade better. Successful lookups are now cached, so an
outage serves the last known price with its date rather than nothing: a farmer
deciding when to sell is better served by "cotton was Rs 7,200 a quintal in
Nagpur on 25 August, confirm today's rate at the mandi" than by silence. It
does not help the first outage, which is this one, but it means the next is not
a blackout.

### Most government agricultural sources cannot be fetched

Of eight candidate sources probed, one downloaded cleanly. `agricoop.gov.in`
refuses connections outright; the Soil Health Card portal renders three words
of text without JavaScript; several documented PDF links return HTML shells or
404s. This is presumably why the original design crawled once and stored the
result to S3. Documents that will not download have to be saved from a browser
and ingested from disk.

### Open-Meteo rate limits by IP, and the IP is shared

Weather worked locally in 2s and failed in production. The cause was
`429 Daily API request limit exceeded` — a **per-IP** limit on a Render free
instance whose egress IP is shared with every other free-tier user. We could
make zero calls and still be locked out, so caching and retries could never fix
it. A second provider (MET Norway, free and keyless) now serves weather when
Open-Meteo refuses; it has no history, and the report says so rather than
presenting a thinner answer as complete.

### One behaviour for every message

The agent classified a message's *topic* but not its *kind*, so statements,
meta-questions, corrections and greetings all became "give agronomic advice".
A farmer writing "I am in Nagpur and I grow cotton" was lectured about sowing;
one asking "how do you know about my soil?" received an explanation of
Walkley-Black titration — a correct account of how soil carbon is measured, and
no answer at all to what was asked.

Adding dialogue acts then **over-corrected**: "my cotton has been attacked by
locusts" is grammatically a statement, and was answered with "noted, what would
you like to ask?" A farmer whose crop is being eaten is asking for help
whatever the grammar. Naming a topic at all now routes as a question.

### The system did not know what it could do

Asked "how can you help me?", the agent named three data providers -
OpenLandMap, Open-Meteo, data.gov.in - and omitted pest doses, government
schemes, sowing advice and photo input. Asked directly about welfare schemes it
replied that it had no information on them, while the store held eighty-six
passages covering four.

The cause was that `provenance.py` was written when three tools existed and was
never derived from the running system. It listed a fixed set of sources, so as
the dose table and the document store were added it went stale silently, and
the mechanism built to make the assistant honest about itself became the thing
telling farmers it could not do what it could.

Capabilities are now assembled from live state: how many registered uses are
loaded, which schemes the documents actually cover, whether Earth Engine is
configured. And they are phrased for a farmer - "which pesticide is approved
for this pest, at what dose" rather than the name of the service the figure
came from. A farmer wants to know you can help with pests; they do not care
that OpenLandMap exists until they ask where a number came from.

### Refusing without offering is the unhelpful half of honesty

A general question - "tell me about welfare schemes" - matches no passage above
the similarity floor, because it names nothing specific. The honest answer was
"I have no information on that", which was both true of the retrieval and false
of the system.

Where retrieval finds nothing, the model is now told which subjects the store
does cover, so it offers them instead of stopping at the refusal.

### The profile was loaded but not used

Facts absent from the current message did not fall back to the stored profile,
and — the part that actually bit — the advice prompt and the tools were still
handed the raw extraction rather than the merged result. So the agent asked
which crop, for a farmer whose cotton had been on file since their first
message.

---

## 4. Two kinds of memory

Deliberately separate, because they have different lifetimes:

- **Profile facts** — location, coordinates, crops, language. Persist
  indefinitely. Merged, never overwritten: a message mentioning no location
  must not erase the one learned last week.
- **Session context** — recent turns, expiring after 48h, so a thread from
  three weeks ago cannot be mistaken for context on today's question.

Conflating them causes stale context to resurface, or a location to be
forgotten because a chat window lapsed.

Location is captured **opportunistically** — the parser already extracted it
from every message and discarded it. If still unknown after answering, one
short invitation is appended, within the existing SMS segment budget, and not
repeated for 24 hours. It never blocks an answer.

---

## 5. SMS is not a text box

- **Segments, not characters.** Latin packs 153 characters per segment;
  Devanagari and Gurmukhi force UCS-2 at 67. Capping on characters bills
  Indic-language farmers more than twice as much for the same message.
- **Markdown is noise.** Answers are flattened to plain text.
- **Truncation must land on a sentence.** A pesticide dose cut mid-instruction
  is worse than a shorter complete one.
- **RCS is invisible.** Google Messages sends Android-to-Android as RCS, not
  SMS, so those messages never reach the gateway. Feature phones — the actual
  target — are unaffected.


---

## 6. Measured results

Every figure below was measured on this system, not estimated. Where a number
is absent it is because the thing was never measured, and that is said plainly
in section 8.

### Latency

| What | Before | After | Change |
|---|---|---|---|
| Rice disease question, end to end | 97.4s | 4.0s | **24× faster** |
| Weather question, end to end | ~97s | 2.4s | **40× faster** |
| LLM failover past a dead model | 64.6s | 1.1s | **59× faster** |
| Soil lookup (3 Earth Engine reads) | ~9s | 1.5s | **6× faster** |
| Repeat weather for the same district | 0.74s | ~0s | cache hit |
| Price question during upstream outage | 90.4s | ~0s after 3 failures | circuit breaker |

Where the 97s actually went, before any of this:

| Step | Time | Share |
|---|---|---|
| mandi prices (data.gov.in, down) | 90.35s | **92.8%** |
| extract slots (LLM) | 2.88s | 3.0% |
| refine query (LLM) | 1.61s | 1.7% |
| synthesise answer (LLM) | 1.60s | 1.6% |
| weather (HTTP) | 0.76s | 0.8% |
| geocode (HTTP) | 0.16s | 0.2% |

The three model calls together were **6.1s of 97.4s**. This is why a
master-worker architecture would have made latency worse: the LLM calls were
never the problem, and adding a router plus per-domain workers multiplies the
only part that was already fast.

Model choice contributed separately: swapping the primary from
`gemini-3.6-flash` to `gemini-3.1-flash-lite` took mean end-to-end answer time
from **23.2s to 2.4s** across three questions, with answers still correct and
specific.

Deployed, warm: **0.4s** health, **1.8–3.5s** for a full answer. Cold, on
Render's free tier: **140s** to first byte, which is why the keep-warm ping
matters and why the SMS bridge retries rather than apologising.

### Efficiency

| | Before | After |
|---|---|---|
| Model calls per question | up to 4 | 3 |
| Tools run per question | all 3, always, sequentially | only those the intent needs, concurrently |
| Cost of intent classification | — | 0 extra calls (rides on the existing extraction) |
| Cost of message-kind classification | — | 0 extra calls (same) |
| Cost of tool selection | — | 0 calls (plain data and functions) |

`kb_router` was a whole model call that the planner replaced. Intent, message
kind and pest all ride on the extraction call that runs regardless, so richer
routing was added while the call count went **down**.

### Grounded data

| | |
|---|---|
| Registered pesticide uses loaded | **2,456** |
| Minimum Support Prices loaded | **20 commodities, 39 aliases** |
| Distinct crops covered | **314** |
| Distinct products | **825** |
| Source | 6 CIB&RC PDFs, as on 31.03.2026 |
| Rows parsed before the misattribution guard | 2,722 |
| Rows kept after it | 2,466 (**256 dropped deliberately**) |
| Product headings missed per 40 pages, before the fix | **30** |
| Insecticide uses retaining a valid waiting period | 504 / 1,102 |

The 256 dropped rows are the point, not a loss: each was a row whose product
heading could not be identified, and keeping them would have credited a dose to
the wrong chemical.

### Correctness defects found and fixed

| Defect | Magnitude |
|---|---|
| Soil organic carbon | reported at **2× true value** — 0.50% printed as 1.00% |
| Soil texture class map | **inverted** — clay read as sand across all 12 classes |
| Pre-harvest intervals | dilution volumes landing in the field, e.g. "500–1000 days" |
| SMS billing for Indic scripts | capped by characters, costing **2.3×** more per segment than Latin |

### Coverage

| | |
|---|---|
| Scripts verified end to end | 4 — Latin, Devanagari, Gurmukhi, Bengali |
| Evaluation cases (language and behaviour) | 28, all passing |
| Database tests (rating schedule) | 6, all passing |
| Embedding dimensions | 768 |
| Embedding separation | 0.814 related vs 0.521 unrelated (cosine) |
| Retrieval corpus | 86 chunks across 5 documents |
| — official (PM-KISAN guidelines, PIB Soil Health Card factsheet) | 57 chunks |
| — reference (Wikipedia: PMFBY, KCC, Soil Health Card) | 29 chunks |
| Retrieval separation, covered vs not | 0.68-0.80 against 0.47-0.58 |

### Cost

| | |
|---|---|
| Recurring infrastructure cost | **$0** |
| Avoided by not using Bedrock's default vector store | **$345–700/month** |

Free tiers throughout: Render hosting, Neon Postgres with pgvector, Nominatim
geocoding, Open-Meteo and MET Norway weather, Earth Engine non-commercial, and
the phone gateway. The only paid item the project needs is Gemini billing, and
that is a quota question rather than an infrastructure one.

### Asking farmers what they thought

Two rules govern when a rating is requested, and they conflict if taken
literally. A rating should be asked while the conversation is fresh - shortly
after it ends - and at most once a month on a day drawn per farmer. Treating
the drawn day as the day to *send* asks a farmer about a conversation from two
weeks earlier that they no longer remember. So the drawn day is the earliest
*eligible* day, and the ask goes out after the first conversation that finishes
on or after it.

The day is drawn per farmer rather than shared, because a single date would put
a month of reminders through one handset in an afternoon.

A rating alone says a farmer was unhappy. Stored beside what the conversation
was about - intent, which tools ran, crop, state, whether a dose was given or
refused, how many turns - it says what *kind* of question goes wrong, which is
the part that can be acted on. Those features cannot be reconstructed later, so
they are written with the number.

Parsing needed a guard. "My 5 acre farm has bollworm" is a question that
happens to contain a digit, and scoring it as a rating would both record
nonsense and leave the farmer's problem unanswered. A bare number now counts
only in a short reply; an explicit form - "4/5", "rating 4", "4 star" - counts
at any length.

Scheduling is driven by an external cron hitting an endpoint rather than a
timer inside the process, because free hosting sleeps and a background loop
would simply stop. The same ping also wakes the service.

---

## 7. Testing

Every bug above was found by a person reading an SMS screenshot. That does not
scale, and it let regressions survive several commits.

`eval/` holds cases derived from real failures, each with machine-checkable
assertions: script matches input, no dose quoted where none is registered,
segment budget respected, no sowing window in the past, the profile's crop used
rather than asked for. Tags allow a subset to run, because each case costs two
model calls and the free tier's daily quota does not fit the suite.

---

---

## 8. What has NOT been measured

Stating this plainly matters more than the numbers above, because the absence
is where the risk sits.

- **Agronomic accuracy.** The evaluation harness checks *properties* — script,
  segment budget, whether a dose was quoted when none is registered. It does
  not check whether the advice is agronomically right. Doses are now grounded
  in the statutory register, but the surrounding guidance is still the model's,
  and nobody qualified has reviewed it.
- **Load.** The original submission claimed 10× peak load at ≥99% delivery.
  Nothing has ever been load tested. One phone, one SIM, and a 20-request daily
  model quota.
- **Delivery rate.** No measurement of how many SMS replies actually arrive.
- **Whether any of it helps.** A rating loop now exists and is live, but it has
  collected two ratings from one farmer. That is plumbing, not evidence. Nothing
  yet records whether a farmer acted on an answer or came back because of it.
- **Retrieval breadth.** Five documents covering four schemes. Retrieval works
  and refuses outside its coverage, but a farmer can ask about far more than
  four schemes. Breadth is limited by what can be obtained, not by the
  machinery.
- **The similarity floor is tuned to this corpus.** It has already been broken
  once by adding documents, and will need re-measuring whenever the corpus
  grows materially.

The honest summary is that the system is measurably faster, measurably cheaper,
and measurably more grounded than it was — and its agronomic quality remains
unmeasured.

---

## 9. Known limits

- **Gemini free tier: 20 requests/day per model.** Two calls per question means
  roughly 30 questions a day across the fallback chain. This is the ceiling on
  real use; everything else is polish until billing is enabled.
- **One phone, one SIM.** Off, offline or out of balance means the service is
  down, and silence looks identical to working.
- **No authentication or rate limiting** on `/agent`.
- **Soil is a 250 m global model**, not a test of the farmer's field. The
  system says so when asked.
- **data.gov.in** has been returning 502 for days. A circuit breaker stops
  farmers waiting out the timeouts.
- **One farmer.** The rating loop, the session features and the eval harness are
  all built and working; what is missing is people using the service. Every
  measurement of quality below the level of "does it behave correctly" waits on
  that.
