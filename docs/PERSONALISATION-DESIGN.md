# The Entry Quiz, the Learner Model, and Where Content Comes From

A design proposal answering three questions you raised:

1. What should the first-setup quiz actually measure?
2. How does Souly decide *how* to explain something?
3. If `lesson_steps` and `questions` are the same for every child, what replaces them?

They turn out to be one question with one answer.

**Nothing here is built.** Decide first.

---

# Part 0 — The trap, named up front

The obvious entry quiz is *"how do you learn best — pictures, sounds, or doing?"*
and then telling the LLM "this child is a visual learner."

**Do not build that.** It is the learning-styles meshing hypothesis, and it is
one of the most thoroughly refuted ideas in education research.

- Pashler et al. (2008, *Psychological Science in the Public Interest*) looked
  for studies using the design that could actually validate it — classify by
  style, randomly assign to methods, same test — and found **essentially one**,
  itself flawed. Properly run studies find no interaction.
- The most favourable recent meta-analysis (2024, 21 studies, n=1,712) reports
  g = 0.31 but **only 26% of outcomes showed the crossover pattern the theory
  requires**, and the authors still conclude the effect is "too small and too
  infrequent to warrant widespread adoption."
- **89.1% of teachers believe it** (37 samples, 15,405 educators, 18 countries).
  So expect a judge, a teacher, or a parent to *ask* for this feature.

That last point is the opportunity. Every other team's "AI personalisation"
will be a VARK quiz with a nicer font. Knowing why that's wrong — and having
built the thing that actually works instead — is a much stronger answer than
having the feature.

And the broader warning: aptitude-treatment interaction research, the whole
field of "match teaching to learner type", largely failed. Cronbach's own
verdict after running the flagship programme was that successive studies find
*different* interactions — a "hall of mirrors." The statistical reason is now
clear: only about **1 in 5 moderated effects replicate**, versus about half of
main effects.

**Exactly one aptitude-treatment interaction has replicated well enough to
build on, and it is the one we need.**

---

# Part 1 — The one thing that does work: prior knowledge × scaffolding

**The expertise reversal effect** (Kalyuga, synthesising dozens of studies):
instructional support that helps a novice actively *harms* someone who already
knows the material, because they have to reconcile redundant guidance with
existing schemas.

Concretely: integrated diagram-plus-text helped novices (**d = 1.67–1.89**) and
*hurt* more knowledgeable learners (**d = −0.44 to −0.88**). Worked examples
beat problem-solving for novices (0.90) and reversed for experts.

And critically — Kalyuga's *adaptive* studies, where you measure expertise then
switch format, produced **d = 0.46** on knowledge gain over a non-adaptive
control. That is a real, replicated, adaptive-instruction effect.

So the axis Souly should personalise on is not *style*. It is
**how much scaffolding this child needs, right now, on this concept.**

Which leads to the good idea.

---

# Part 2 — Dynamic Assessment: measure how they respond to help

Instead of asking a child what kind of learner they are, **give them a problem
and measure how much help they need to get it.**

This is Dynamic Assessment — Vygotsky's zone of proximal development made
operational. The modern, standardised version is **graduated prompts** (the
Leiden group: Resing, Vogelaar, Veerbeek, with Elliott at Durham):

> The child attempts an item. If they're wrong, a **fixed, pre-written ladder**
> of increasingly explicit prompts fires — general/metacognitive first, then
> task-specific, then a full worked model. **The score is how many prompts they
> needed.**

Read that again and look at what we already built. Our hint ladder is
nudge → worked example → step-through → answer. **That is a graduated-prompts
instrument.** The onboarding activity and the tutoring mechanic are the same
machine.

## What the prompt count buys us

- It correlates **r = .45–.54** with teacher judgements of how independently a
  child works — recovered from ~15–20 minutes of data.
- It adds real unique variance over static tests: **1–21%** for word-reading
  growth, **4–33%** for morphological DA → comprehension.
- **Caffrey, Fuchs & Fuchs (2008), 24 studies:** DA beats static testing
  *conditionally* — and the conditions are ours. It held **(a) for students
  with disabilities** rather than typically-achieving ones, and **(b) when the
  prompts were standardised rather than improvised.**
- **The ADHD finding is the one to put on a slide.** Cornoldi's group (n=34
  ADHD, 27 typically developing, ages 8–10): children with ADHD-Combined
  showed **low static scores but high dynamic scores** — 33% had that profile.
  A static test systematically under-reads them. A prompt-count measure
  recovers them.

## What it produces

Veerbeek & Vogelaar (2025) ran the closest thing to our onboarding — a
**training-only** graduated-prompts design, no pretest, 15–20 minutes, n=66,
mean age 10.9 — and sorted children into three **instructional need** profiles:

| Profile | Signature | What Souly should do |
|---|---|---|
| **Low instruction** | Solves with few or no prompts | Lead with the question. Minimal scaffolding — remember expertise reversal: extra support actively hurts here. |
| **Metacognitive** | Needs "what's changing?" nudges, rarely needs content re-taught | Scaffold *strategy*, not content. "What did you try first?" Keep tier 1 and 2. |
| **Task-specific** | Needs the domain content re-explained before they can proceed | Re-teach the concept before the question. Start at worked examples. |

Children in the task-specific group scored significantly lower on standardised
maths and reading — the profile tracks something real.

**This is the answer to "how does it decide the way to explain with?"**
Not from a self-reported style. From measured responsiveness to help.

## Where I have to be honest

Elliott, Resing & Beckmann (2018) — *from DA's own leading advocates* — titled
their review "**a case of unfulfilled potential?**" Their conclusion: fifteen
years on, **no published studies show that DA recommendations produce
meaningful educational gains.** The assessment is good. The
assessment→instruction link is largely unevidenced.

And a 2025 feasibility study in a heterogeneous clinical sample is a warning:
**22% data loss** to dropout and technical failure, sessions budgeted at 20–30
minutes running up to **2.5 hours**, two participants quitting from
frustration, and test–retest reliability *worse* in the trained group.

So: build it, log it, and treat the profile as **a prior we keep correcting**,
never a diagnosis. Which the cold-start research says anyway.

---

# Part 3 — Keep the quiz short. The research is emphatic.

This is the finding that should shape the whole design:

- Yudelson, Koedinger & Gordon: individualising a learner model's **starting
  point gives only marginal gains. Individualising the *learn rate* gives
  large, consistent gains.** Where a child starts matters far less than how
  fast they move.
- Pardos & Heffernan's best per-student prior wasn't a separate test at all —
  it was **a percent-correct heuristic from the student's own first few
  answers.**
- Empirically, model accuracy for an unseen student climbs from ~0.50 to
  **~0.75 by around 20 answered questions.**
- Adaptive testing needs ~**22 items** to pin down an ability estimate.

Translation: **twenty minutes of real tutoring tells you as much as a twenty
minute test, and the child enjoys it more.** An eight-minute onboarding is
worth it as a cold-start prior and as a way to set the tone. Anything longer
is spending the child's goodwill to buy information the first session gives
you free.

---

# Part 4 — The entry quiz I'd actually build

**Framing first, because for this population it matters more than content.**

Courchesne et al. (2015) is the study that should govern the tone. Thirty
minimally-verbal autistic children on the WISC-IV: **zero of thirty completed
it.** Under a strength-informed protocol — familiar setting, non-verbal
instructions, movable pieces, and **1–6 short sessions averaging 3.8** — 26 of
30 completed, and 65% of those scored at or above the 5th percentile. Almost
the entire gap was protocol, not ability.

Add: intolerance of uncertainty explains ~**45%** of sensory-sensitivity
variance in autistic children (Wigham 2015); test anxiety significantly
moderates dynamic-testing scores (Vogelaar 2017); demand avoidance means **the
request to "do a test" is itself the aversive thing**, independent of content;
and masking means some children will perform *above* their sustainable level
on day one and crash later.

So the design rules:

- **Never call it a test.** Frame it as the child helping Souly: *"Help me
  learn how you like things explained."* The child is assessing us.
- **No score, no timer, no right/wrong sound, no progress pressure.**
- **Show the whole map before starting.** "Six puzzles, about eight minutes,
  then we're done. Nothing is saved as a mark." Uncertainty is the trigger.
- **Declarative, not imperative.** "I wonder what comes next" beats "Complete
  the sequence."
- **Everything skippable**, scored as missing, never as failure.
- **Hard wall-clock cap.** The feasibility study's 20 minutes became 2.5 hours.
- **Genuine choice** of 2–3 puzzle themes — and use their interests.

## The five parts, ~8 minutes

### 1. Warm-up and interests (~60s) — no measurement
Pick an avatar. Pick what you like: dinosaurs, football, space, animals,
Minecraft, drawing, music. Multi-select, skippable.

Not decoration. Embedding a child's focused interest in instruction is the
best-evidenced motivation lever we have for this population — **20 of 20
studies showed engagement gains** (Gunn & Delafield-Butt 2016). This feeds
generated examples forever after.

### 2. Graduated-prompts core (~4 min, 5–6 items) — the main instrument
Figural analogies and series completion — the task families the DA literature
uses, and deliberately **not curriculum content**, so it measures reasoning
under help rather than what school they went to.

Each item, on a wrong attempt, fires **the fixed four-rung ladder**:

1. Metacognitive nudge — *"What's changing each time?"*
2. Second metacognitive — *"Try looking at just the first two."*
3. Task-specific — *"The shapes are rotating. By how much?"*
4. Full model — Souly solves one exactly like it, then offers another.

**The prompts must be pre-written and identical for every child.** Caffrey's
review found DA's predictive advantage lives specifically in *non-contingent*
— standardised — feedback. If the LLM improvises the onboarding prompts, the
score means nothing. (Improvising is exactly right during normal tutoring;
it's wrong here.)

Logged per item: prompt level reached, latency to first attempt, latency after
each prompt, self-corrections, whether rung 1 alone ever sufficed.

### 3. Decoding vs listening split (~2 min) — the one real modality branch
Two short comprehension items on equivalent material: one **read silently**
(timed, tap to answer — no speech recognition needed), one **heard as audio**.

This is the Simple View of Reading: comprehension = decoding × language
comprehension. The dissociation is real and large — Foorman et al. (2018)
explain 68–78% of reading-comprehension variance in grades 1–3 and ≥97% in
grades 4–10, with unique decoding variance falling as grade rises. A dyslexic
reader has weak decoding and intact listening comprehension; a "poor
comprehender" is the reverse.

**A big gap in favour of audio is not a "learning style" — it's a measured
decoding deficit, and read-aloud is the evidenced accommodation for it**
(Wood et al. 2018, 22 studies, **d = 0.35** on comprehension).

That distinction is worth rehearsing for the judges, because it looks
superficially like the thing I just told you not to build, and it is the
opposite of it.

### 4. Prior-knowledge probe (~90s, optional per subject)
Kalyuga's **rapid first-step** method: show a task, ask only for the *first
step* you'd take. Correlates up to **r = 0.92** with conventional tests at
**3–5× less testing time**.

Seeds the mastery model per topic and sets the initial scaffolding level.

### 5. Sensory and format preferences (~60s) — self-report, honestly labelled
Sound on or off. Motion on or off. Bright or calm colours. Big or normal text.
Souly's voice: try two, pick one.

These are **accessibility settings collected pleasantly**, not a psychological
profile. Label them as preferences internally so nobody later mistakes them
for measured traits.

## What comes out

```json
{
  "instruction_need": "metacognitive",     // low | metacognitive | task_specific
  "confidence": 0.35,                       // deliberately low on day one
  "mean_prompts_needed": 2.1,
  "rung1_sufficient_rate": 0.4,
  "median_first_attempt_ms": 8200,          // calibrates the stall detector
  "latency_variability": 0.6,
  "modality_gap": +0.4,                     // positive = listening > reading
  "reading_efficiency_pct": 35,
  "persistence": "gives_up_early",
  "interests": ["dinosaurs", "football"],
  "possible_masking": false,
  "source": "onboarding",
  "measured_at": "2026-08-19T..."
}
```

## Two enhancements you asked me to add

**A. It solves a cold start we already have.** `tutor.py` currently guesses a
stall threshold until it has four samples of a child's step time. The
onboarding gives us `median_first_attempt_ms` on day one, so Souly knows from
the first minute how long *this* child normally takes to think — and doesn't
interrupt them.

**B. Model the two-sided error.** Day-one measurement is wrong in *both*
directions: anxiety and novelty push scores down, masking pushes them up.
Flag a child who finishes fast with zero prompts **but** shows high latency
variability or hesitancy as a possible masker, and don't immediately pitch
everything at the highest level. Courchesne needed ~3.8 sessions before
performance stabilised — **expect the estimate to move, mostly upward**, and
store it with low confidence so it can.

---

# Part 5 — Your content critique, and what I think replaces `lesson_steps`

You're right, and the fix is a separation we don't currently make.

**Today:** `lesson_steps` holds authored prose. Every child sees the same five
paragraphs in the same order. That is a slideshow with a robot beside it.

**The change: split the source of truth from the way it's said.**

```
   AUTHORED, VERIFIED, SHARED              GENERATED, PER CHILD, CACHED
   ────────────────────────────            ────────────────────────────
   concepts        what must be learnt
     ↕ prerequisites  and in what order
                                    ──▶    renditions
   curriculum_sources                      how THIS concept is explained
     the real syllabus corpus,             to THIS child, right now
     chunked and RAG-indexed         ──▶   practice items generated
                                           from the same source
```

**`concepts`** replaces `lesson_steps` as the spine. A concept is small and
checkable — "a fraction is a part of a whole", "the denominator is the number
of equal pieces" — with **prerequisite edges** to other concepts. Authored by
your team, verified, stable. This is what mastery attaches to.

**`curriculum_sources`** is your real syllabus: the actual material, chunked
and indexed. Retrieval draws from here. Nothing is taught that isn't in it.

**`renditions`** is the new thing. The first time a given child meets a given
concept, Souly generates an explanation from the source chunks, pitched using
that child's learner profile — and **caches it against that child**.

That gets you both properties at once:

- **Different children genuinely get different explanations.** Two students on
  the same concept see different words, different examples, a different number
  of steps, possibly a different order.
- **The same child gets the same explanation twice.** Which matters enormously
  here — a tutor that rephrases everything on every visit is exactly what the
  predictability research warns against for autistic learners.

Regenerate a rendition when the child asks ("say it another way"), or when
their profile shifts materially. Never at random.

Same for `questions`: generated per child from the concept's sources, cached,
validated by the checker we already built, with the verified bank as the floor
when generation fails.

**Sequencing gets personalised too** — the path through the concept graph is
computed from mastery and prerequisites, so two children don't even meet the
concepts in the same order. That's the other half of your "same sequence for
every child" complaint, and the concept graph is what makes it possible.

## One more upgrade while we're here

Our mastery counter moves **+0.12 correct / −0.05 wrong**. On a four-option
question, a pure guess is right 25% of the time — the counter cannot tell a
lucky guess from understanding.

**Bayesian Knowledge Tracing** (Corbett & Anderson) is the standard fix: a
two-state model per concept with four parameters — prior, learn rate, guess,
slip — updated by Bayes after each answer. Our counter is literally BKT with
the Bayes step deleted.

Don't go further than that. Khajah, Lindsey & Mozer found extended BKT
"indistinguishable" from Deep Knowledge Tracing while staying interpretable,
and Gervet & Koedinger found simple models *win* on small datasets. At our
scale BKT is the right answer, and it hands the LLM an honest, explainable
mastery number per concept.

---

# Part 6 — Your hypothesis: pre-prepare, or decide at explanation time?

You asked whether the LLM should prepare the curriculum's approach after the
setup quiz, or decide when explaining.

**Decide at explanation time. Cache the result per child.**

Pre-baking the whole curriculum has three problems, and the first is fatal:

1. **Day one is when the profile is at its worst.** Confidence is lowest,
   anxiety and novelty are depressing performance, masking may be inflating
   it, and Courchesne's work says it takes ~4 sessions to stabilise. Baking
   permanent decisions from that snapshot locks in the least reliable estimate
   we will ever have.
2. It's expensive — every concept × every child, most never opened.
3. It can't use what the child did five minutes ago.

Deciding purely live has its own costs — latency on a MiFi, cost per call,
and inconsistency between visits.

**Cache-on-first-encounter gets both.** Generation happens lazily, using the
profile as it stands at that moment; the result is stable for that child
thereafter; and the profile keeps improving underneath from real hint-ladder
telemetry, which the cold-start research says is where the accuracy actually
comes from.

So the honest version of your hypothesis is: **the entry quiz sets the opening
pitch; the tutoring itself does the real learning about the learner.**

---

# Part 7 — What I need you to decide

1. **Graduated-prompts onboarding instead of a preference quiz** — yes?
   *(My view: yes. It's the strongest idea in this document and it reuses the
   hint ladder we already have.)*
2. **Concepts + sources + per-child cached renditions**, replacing authored
   `lesson_steps`? This is a real schema migration and the biggest change yet.
   *(My view: yes — it's the thing that makes "personalised" true.)*
3. **BKT instead of the ± counter?** *(My view: yes, it's contained.)*
4. **How much real curriculum can you get, and in what form?** This now
   matters more, not less — generated explanations are only as good as the
   corpus behind them. PDFs? Textbook chapters? Teacher notes?
5. **Onboarding length.** I've specced ~8 minutes. The research says shorter
   is defensible; the demo may want the full thing visible.
6. **Do we A/B it?** Logging profile-on vs profile-off would let us say
   something evidenced at the competition rather than asserted. Cheap to build
   now, impossible to retrofit.

---

## Sources

**Learning styles and ATI**
- [Pashler, McDaniel, Rohrer & Bjork (2008), Learning Styles: Concepts and Evidence](https://journals.sagepub.com/doi/full/10.1111/j.1539-6053.2009.01038.x)
- [Learning-styles matching meta-analysis (Frontiers, 2024)](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2024.1428732/full)
- [Newton & Salvi (2020), prevalence of the neuromyth among educators](https://www.frontiersin.org/journals/education/articles/10.3389/feduc.2020.602451/full)
- [Cronbach & Snow, Aptitude-Treatment Interaction — overview](https://www.instructionaldesign.org/theories/aptitude-treatment/)
- [von Hippel & Schuetze (2025), why interaction effects rarely replicate](https://edworkingpapers.com/sites/default/files/ai25-1116.pdf)
- [Kalyuga (2007), The Expertise Reversal Effect](https://www.uky.edu/~gmswan3/EDC608/Kalyuga2007_Article_ExpertiseReversalEffectAndItsI.pdf)
- [Expertise reversal meta-analysis (2025)](https://www.sciencedirect.com/science/article/pii/S0959475225000660)

**Dynamic assessment / graduated prompts**
- [Veerbeek & Vogelaar (2025), Dynamic Testing of Instructional Needs: A Training-Only Graduated Prompts Approach](https://journals.sagepub.com/doi/full/10.1177/07342829241287947)
- [Caffrey, Fuchs & Fuchs (2008), The Predictive Validity of Dynamic Assessment](https://journals.sagepub.com/doi/10.1177/0022466907310366)
- [Dixon et al. (2023), Dynamic assessment as a predictor of reading development](https://link.springer.com/article/10.1007/s11145-022-10312-3)
- [Static and Dynamic Assessment of Intelligence in ADHD Subtypes (Frontiers, 2022)](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2022.846052/full)
- [Vogelaar et al. (2021), Computerized dynamic testing of reasoning by analogy](https://onlinelibrary.wiley.com/doi/abs/10.1111/jcal.12512)
- [Resing et al., Progression and individual differences after dynamic testing](https://pmc.ncbi.nlm.nih.gov/articles/PMC7065092/)
- [Computerized Dynamic Assessment of Analogical Reasoning in Children with Autism (2025)](https://www.mdpi.com/2076-328X/15/9/1188)
- [Elliott, Resing & Beckmann (2018), Dynamic assessment: a case of unfulfilled potential?](https://scholarlypublications.universiteitleiden.nl/access/item:2970836/download)
- [Dynamic Testing in a Heterogeneous Clinical Sample: A Feasibility Study (2025)](https://doi.org/10.3390/bs15101342)
- [Shute, Stealth Assessment](https://files.eric.ed.gov/fulltext/ED612156.pdf)

**Assessing autistic children**
- [Courchesne et al. (2015), Autistic children at risk of being underestimated](https://link.springer.com/article/10.1186/s13229-015-0006-3)
- [Koegel, Koegel & Smith (1997), Variables related to differences in standardized test outcomes](https://link.springer.com/article/10.1023/A:1025894213424)
- [Szarko et al. (2013), Examiner familiarity effects for children with ASD](https://www.tandfonline.com/doi/abs/10.1080/15377903.2013.751475)
- [Wigham et al. (2015), Intolerance of uncertainty, sensory sensitivities and anxiety](https://link.springer.com/article/10.1007/s10803-016-2721-9)
- [Vogelaar et al. (2017), Dynamic testing and test anxiety](https://bpspsychub.onlinelibrary.wiley.com/doi/abs/10.1111/bjep.12136)
- [Demand avoidance in children — scoping review (Frontiers, 2024)](https://www.frontiersin.org/journals/education/articles/10.3389/feduc.2024.1230011/full)
- [Children's Masking Questionnaire pilot](https://www.frontiersin.org/journals/psychiatry/articles/10.3389/fpsyt.2026.1893936/full)

**Learner modelling**
- [Yudelson, Koedinger & Gordon, Individualized Bayesian Knowledge Tracing](https://www.cs.cmu.edu/~ggordon/yudelson-koedinger-gordon-individualized-bayesian-knowledge-tracing.pdf)
- [Pardos & Heffernan, Prior Per Student BKT](https://people.csail.mit.edu/zp/papers/UMAP_final.pdf)
- [Cold-start in knowledge tracing (arXiv 2505.21517)](https://arxiv.org/pdf/2505.21517)
- [Khajah, Lindsey & Mozer, How deep is knowledge tracing?](https://arxiv.org/abs/1604.02416)
- [Gervet & Koedinger, When is deep learning the best approach to knowledge tracing?](https://theophilegervet.github.io/assets/pdf/gervet2020deep.pdf)

**Reading and modality**
- [Foorman et al. (2018), decoding vs language comprehension, grades 1-10](https://www.sciencedirect.com/science/article/abs/pii/S1041608018300414)
- [Wood et al. (2018), text-to-speech and reading comprehension meta-analysis](https://journals.sagepub.com/doi/10.1177/0022219416688170)
- [Gunn & Delafield-Butt (2016), teaching using restricted interests](https://journals.sagepub.com/doi/abs/10.3102/0034654315604027)
