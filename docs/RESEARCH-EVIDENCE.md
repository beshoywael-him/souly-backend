# What the Evidence Says — and What Should Live in Souly

A decision document. Every practice below is something Souly *could* do. For
each: what it is, how strong the evidence is, what it would mean in our app,
and my recommendation.

**Nothing here has been built.** This is for us to argue about first.

How to read the recommendation column:

| Mark | Meaning |
|---|---|
| **ADOPT** | Strong evidence, clear fit, low cost. I'd build it. |
| **ADAPT** | Good idea but the obvious implementation is wrong. Details matter. |
| **DISCUSS** | Genuine judgement call. Your team should decide, not me. |
| **AVOID** | Popular, intuitive, and not supported — or actively harmful. |

---

# Part 1 — The five findings that should change the design

If you read nothing else, read these.

## 1.1 The attention-flag camera is the riskiest thing we're building

This is the hardest thing in this document and I'd rather say it now than
after we've built it.

Our premise is: camera detects a student's focus drifting → flag → robot
re-engages them. Three separate research strands say that specific loop can
harm autistic students.

**Gaze aversion increases with cognitive difficulty.** Doherty-Sneddon et al.
(2012) found autistic children look away *more* as a task gets harder, and
that this is functional — looking away reduces load so they can think. A
camera that reads "looked away" as "disengaged" will interrupt children
**precisely when they are concentrating hardest.**

**Stimming aids concentration.** Kapp et al. (2019): autistic adults report
stimming helps them focus and self-regulate, and being stopped produced
"anger", "shame", feeling "belittled". A movement-based drift detector will
flag self-regulation as inattention.

**Interruption is unusually costly.** Monotropism theory (Murray, Lesser &
Lawson 2005) describes autistic attention as a deep "tunnel" rather than a
spread. Buckle et al. (2021), interviewing 32 autistic adults, found
interruption often means the task **cannot be resumed at all** — "I can't get
back to the place I was before" — and that fear of this stops people starting
tasks. McDonnell & Milton (2014) argue interrupting flow states produces
"meltdown, shutdown, and panic attacks."

And underneath all of it, Milton's double empathy problem (2012): "this child
is disengaged" is a cross-neurotype *inference* by an observer, not a fact
about the child.

**But here's the constructive part.** The same Buckle paper found that
external prompting was the single **most effective** intervention for autistic
inertia — being stuck and unable to start. Participants wanted a "stuck
buddy". Alarms and generic reminders were useless; a responsive prompt from
outside was gold.

So the intervention is right and the **trigger** is wrong.

| | Current design | Proposed |
|---|---|---|
| Trigger | Gaze/head direction over ~5s | **No progress** — no input, repeated wrong answers, thrashing — over minutes |
| Timing | Whenever detected | Only at an item boundary, never mid-item |
| First move | Robot addresses the student | Ambient cue first (a soft light), escalating only if ignored |
| Student control | None | An "I'm working, leave me" control the robot obeys |
| Naming | "attention detection" | "**stall** detection" — we detect observable orientation, not attention |

This also makes a better competition answer, not a worse one. "We detect when
a student is stuck, not when they look away, because autistic children look
away *more* when they're thinking hard" is a far stronger thing to say to a
judge than a demo of a camera catching someone glancing out of a window.

**Recommendation: ADAPT — keep the CV rig, re-spec the trigger.**

## 1.2 An AI that answers questions makes students worse

The most important study for our AI feature. Bastani et al., **PNAS 2025**:
~1,000 students, three groups, four maths sessions.

| Group | During practice | On a later exam **without** AI |
|---|---|---|
| Plain ChatGPT | **+48%** | **−17% vs control** |
| Guardrailed "GPT Tutor" | **+127%** | No different from control |
| No AI | baseline | baseline |

Students with unrestricted AI looked dramatically better while using it and
came out **worse than students who never had it.** 67% of their messages were
just asking for the answer.

Two guardrails produced the difference:

1. **The tutor never gives the answer.** It gives hints.
2. **The worked solution and the common wrong answers for that specific item
   are in the system prompt.**

Note also that even the *good* tutor produced no transfer gain over controls.
Engagement metrics massively overstate learning. If we demo "look how engaged
the student is," we are demoing the thing that is least informative.

**Recommendation: ADOPT both guardrails, non-negotiable.** Our current chat
answers questions directly. That has to change.

## 1.3 Autistic students often don't ask for help — so "ask if you're stuck" won't work

This one lands directly on the flow you asked for: "when a student picks a
lesson, they can ask when there's a part they don't understand."

The intent is right. The passive implementation will fail, for two reasons.

**Metacognitive monitoring.** Grainger, Williams & Lind (2016): autistic
children's confidence judgements were significantly less accurate, and they
"used monitoring to influence control processes significantly less than
neurotypical children." Translated: the link between *not understanding* and
*doing something about it* is weaker. The child may not know they're lost.

**Social cost.** Asking for help is an unscripted social act, often under
anxiety, with unclear rules about when it's allowed. The camouflaging
literature (Cook et al. 2021) documents children actively concealing
difficulty at school.

So a button labelled "Ask Souly if you don't understand" will be pressed by
the students who least need it.

**What actually raises help-seeking:**

- The **system offers** rather than waiting to be asked. After N seconds of no
  progress, or after two wrong attempts, offer a hint unprompted.
- Help is **routine, not exceptional** — every explanation has a "show me more"
  affordance that everyone uses, so using it signals nothing.
- Help is **free**. Never cost stars, never break a streak, never show a "you
  used 3 hints" tally.
- Help is **tiered**, so asking doesn't mean surrendering: nudge → example →
  step-by-step → answer with explanation.
- The help routine is **scripted and identical every time** — same words, same
  place, same shape. Predictability is the point.
- **Detect stuck from behaviour**, not self-report.

**Recommendation: ADOPT — build the merged flow, but system-initiated.**

## 1.4 Landscape helps more than you might think

You asked for landscape for layout reasons. It also solves a real learning
problem.

**Spatial contiguity.** Schroeder & Cenkci (2018) meta-analysed 58
comparisons (n=2,426): integrating related material spatially beats separating
it, **g = 0.63**. That's a large effect for a layout decision.

Right now, asking Souly a question means leaving the lesson and going to a
separate chat screen. That is textbook split attention — the child has to hold
the lesson in working memory while looking at a different page.

Landscape lets the explanation stay on the left and Souly answer on the right,
**both visible at once**. The question is asked *about the thing still on
screen.* That single change is probably worth more than everything else in
this section.

**Recommendation: ADOPT.**

## 1.5 Plan the ending now

Moxie was a social robot for children, explicitly marketed for autistic kids.
In December 2024 its company folded, and because every feature depended on
their cloud, every unit **bricked within days**. No refunds. Parents filmed
children crying over their "best friend". It's logged in the OECD AI Incidents
Monitor, and there's now a CHI 2026 paper ("RIP Moxie") on managing emotional
detachment at product end-of-life.

We are building a robot companion for a competition. Some of these children —
your teammates now, real students later — may form an attachment. Then the
project ends.

What the follow-up literature recommends:

- **Offline degraded mode** — it must still do something without our server.
- **Never script permanence.** No "I'll always be here", no "best friend",
  no "I missed you."
- **Say up front** what this is: a study buddy for a set period.
- **Plan a closing ritual** rather than a silent switch-off.

**Recommendation: ADOPT — it's cheap now and impossible to retrofit.**

---

# Part 2 — The full catalogue

## 2.1 Structure and predictability

Intolerance of uncertainty correlates with anxiety at **r = .62** in autistic
people (Jenkinson, Milne & Thompson 2020) — roughly 38% of anxiety variance.
Predictability isn't a nicety.

| Practice | Evidence | In Souly | Rec |
|---|---|---|---|
| **Visual schedule** — the session's steps visible up front, completed ones struck through | TEACCH; visual supports is an NCAEP evidence-based practice | A persistent strip: "3 things today: lesson · practice · game." Always visible in landscape. | **ADOPT** |
| **First–Then** | TEACCH minimal case | "First this explanation, then you try one." | **ADOPT** |
| **Transition warnings / visual countdown** | Dettmer et al. 2000; Schmit et al. 2000 | Warn before a screen changes. Countdown by *removing blocks*, not by clock time. | **ADOPT** |
| **"Finish later" box** | IIDC practitioner guidance | A child can park a lesson and it stays exactly where they left it — respects the need to complete. | **ADOPT** |
| **Stable layout** | Home Office guidance; Pavlov 2014 | Never move a control. No surprise new activity types. Announce changes in-app. | **ADOPT** |
| **Work systems** — what, how much, when finished, what next, answerable without asking | TEACCH core | Every screen answers all four. | **ADOPT** |

## 2.2 The explanation itself

| Practice | Evidence | In Souly | Rec |
|---|---|---|---|
| **Segmenting** — one idea per screen, child advances | Rey et al. 2019 meta: retention d=0.32, transfer d=0.36 | Already roughly right. Keep 30–90s chunks. | **ADOPT** |
| **Spatial contiguity** — labels inside diagrams, hints next to the item | Schroeder & Cenkci 2018, g=0.63 | Drives the landscape decision. Hints must appear *beside* the content, never on another screen. | **ADOPT** |
| **Coherence / no seductive details** | Sundararajan & Adesope 2020 | Confetti, bouncing robot, celebration animations must be **outside** the instructional frame — reward moments only, never while explaining. | **ADAPT** (we currently violate this) |
| **Text + narration together** | Adesope & Nesbit 2012: g=+0.29 vs narration alone, but **g=0.06 (ns)** when pictures/animation present | Nuanced. Fine to show text while reading aloud — **unless** there's a competing diagram, in which case narrate and show only short labels. | **ADAPT** |
| **Word-level highlighting during read-aloud** | Wood et al. 2018: TTS for reading disabilities d=0.35 | Highlight each word as it's spoken so the two channels index each other. | **ADOPT** |
| **Worked examples before practice** | Core cognitive load finding | Show a solved one, then a similar one to try. | **ADOPT** |

## 2.3 Motivation

| Practice | Evidence | In Souly | Rec |
|---|---|---|---|
| **Embed the child's focused interest in the content** | Gunn & Delafield-Butt 2016 — 20 studies, 91 children: **all 20** showed motivation/engagement gains; 15 showed better social engagement | Capture interests at onboarding (dinosaurs, trains, Minecraft, football). Word problems, examples and characters use them. | **ADOPT** |
| **Use interests as a *reward* for compliance** | Same review prefers intrinsic embedding; autistic co-authors (Bayoumi et al. 2025) warn it "could predispose young people toward complying with others' suggestions" | Don't. Embed, never bribe. | **AVOID** |
| **Immediate per-item feedback** | Gaastra et al. 2016: consequence-based interventions had the largest effect on off-task behaviour (M_SMD 1.82) | Feedback within ~1s, before the next item. Never batch at the end. | **ADOPT** |
| **Short work bouts with a countable end** | ADHD literature | "4 more and you're done" beats a spinner. | **ADOPT** |
| **Streaks and variable rewards** | Screen-time/autism review (JADD 2024) flags higher over-engagement risk — evidence "very low" but credible; disengagement is the hardest moment | Our current day-streak mechanic pressures a child to return daily. Reconsider. | **DISCUSS** |
| **Self-monitoring widget** — child rates their own focus, sees a trend | Gaastra: self-regulation interventions M_SMD 3.61 in single-subject designs | Cheap to build, strong effect size. | **DISCUSS** |

## 2.4 Sensory and visual design

Sources: UK Home Office "Designing for users on the autistic spectrum",
Pavlov (2014), Britto & Pizzolato's GAIA guidelines, WCAG 2.2.

| Practice | In Souly | Rec |
|---|---|---|
| **Muted colours, not bright saturated ones** | Our purple is heavily saturated. Grandgeorge & Masataka (2016) found autistic children specifically dislike high-luminance yellow and prefer greens/browns. Offer a muted theme; consider making it default. | **DISCUSS** |
| **No autoplay of audio or video** | Read-aloud must be a toggle, not automatic. | **ADOPT** |
| **Respect `prefers-reduced-motion` + in-app toggle** | Already built. Keep. | **ADOPT** ✓ |
| **Sans-serif, ≥16px, generous letter/line spacing** | Zorzi et al. 2012 (PNAS): extra letter spacing improved reading speed and roughly **halved errors** in dyslexic children, no training needed. | **ADOPT** |
| **Left-aligned, never justified; 60–70 chars per line** | British Dyslexia Association 2023 style guide. Landscape makes over-long lines a real risk — cap the measure. | **ADOPT** |
| **Off-white background, not pure white** | BDA — white can "dazzle". | **ADOPT** |
| **Buttons with icon *and* text** | Home Office: "make buttons descriptive… not vague and unpredictable". | **ADOPT** |
| **One primary task per screen, no clutter** | Home Office, Pavlov. | **ADOPT** |
| **OpenDyslexic font** | Wery & Diliberto (2017) found **no** improvement in reading rate or accuracy, and no participant preferred it. Kuster et al. (2018) same for Dyslexie. Any benefit comes from the spacing they bundle — which we can provide with a normal sans-serif. | **AVOID** as a feature claim; harmless as an optional preference |
| **Coloured overlays / tinted backgrounds as a dyslexia treatment** | Ritchie et al. (2011, *Pediatrics*) and Griffiths et al. (2016) find no reliable benefit. | **AVOID** as a claim; fine as a preference |

## 2.5 Timing

| Practice | Evidence | In Souly | Rec |
|---|---|---|---|
| **No countdown timers on comprehension** | Zapparrata et al. 2023 meta (44 studies): autistic people are slower across the board, **g = .35**. A timer systematically penalises the cohort we serve. | Remove speed from scoring entirely. | **ADOPT** |
| **Generous, per-child response latency before a hint fires** | Doherty-Sneddon 2012 — a child looking away at second 4 is probably mid-computation | Measure each child's own baseline; don't use a global constant. | **ADOPT** |
| **Enforced short delay before answering is accepted** | Dyer, Christian & Luce (1982): a 3s delay improved accuracy in autistic children | Interesting, low cost, slightly counterintuitive. | **DISCUSS** |
| **"Are you still there?" idle prompts** | Contradicts everything above | Remove, or set at minutes. | **ADOPT** (removal) |

## 2.6 Access — never gate on one input channel

Sources: ASHA AAC practice portal; Millar, Light & Schlosser 2006; WCAG 2.2.

| Practice | In Souly | Rec |
|---|---|---|
| **Every prompt accepts a non-speech, non-typing response** | Choice tiles, drag/tap-to-place, draw, symbol picker. Never gate progress on the microphone. | **ADOPT** |
| **AAC symbol support layer**, with symbols in stable positions | Match the child's own system if they have one (Widgit / PCS / ARASAAC). Stable position matters — motor plans transfer. | **DISCUSS** (real work) |
| **Full keyboard/switch operability, visible focus ring** | Partly built. Needs finishing. | **ADOPT** |
| **44–48px touch targets** | WCAG 2.2 floor is 24px; go bigger for this cohort. | **ADOPT** |
| **AAC doesn't suppress speech** | Millar et al. 2006 — worth knowing so nobody argues against symbol support "in case it stops them talking." | context |

---

# Part 3 — The merged AI + explanation flow

You asked how the AI should fit into the lesson. Here's my proposal, built
from the findings above.

## The principle

**Souly is not a chatbot bolted onto a lesson. Souly is the lesson's hint
layer.** There is no separate "Ask Souly" destination. Help lives where the
confusion is.

## The screen (landscape)

```
┌──────────────────────────────────┬───────────────────────────┐
│                                  │                           │
│   THE EXPLANATION                │   SOULY                   │
│                                  │                           │
│   One idea. Large text.          │   [robot]                 │
│   Word-highlighted as spoken.    │                           │
│                                  │   Souly speaks here about │
│   [ diagram / visual ]           │   the thing on the LEFT,  │
│                                  │   which stays visible.    │
│                                  │                           │
│   ● ● ○ ○ ○   step 3 of 5        │   ┌─────────────────────┐ │
│                                  │   │ I don't get this    │ │
│   [ ◀ Back ]      [ Next ▶ ]     │   │ Show me an example  │ │
│                                  │   │ Say it another way  │ │
│                                  │   └─────────────────────┘ │
└──────────────────────────────────┴───────────────────────────┘
   Today:  ✓ Lesson    ○ Practice    ○ Game
```

Two things this buys us: the content never leaves the screen when help is
asked (spatial contiguity, g=0.63), and the help options are **always
present**, so using them is unremarkable.

## Help is offered, not requested

Three buttons instead of a blank text box. Blank boxes require the child to
formulate an unscripted question — the exact barrier from §1.3.

- **"I don't get this"** — Souly re-explains *this step* differently. Simpler
  words, or a concrete example, or a different angle.
- **"Show me an example"** — a worked instance.
- **"Say it another way"** — same content, different framing.

Free-text and voice stay available for children who want them. They just
aren't the only door.

## Souly speaks first when the child stalls

No input for a while, or two wrong attempts, or repeated back-and-forth
between steps → Souly offers, unprompted and non-evaluatively:

> "Want me to explain that bit about the denominator again?"

Not "Do you need help?" — that's an evaluation. Offer the specific thing.

## The hint ladder — never the answer

Per Bastani. Four tiers, and the child can stop at any of them:

1. **Nudge** — "Look at the bottom number again."
2. **Example** — a solved one just like it.
3. **Step-by-step** — walk it through with the child answering each step.
4. **Answer + why** — only after 3, or if the child asks twice more.

Souly must not skip to 4. The system prompt carries the worked solution and
the known wrong answers for *this specific item* — that's the second Bastani
guardrail, and it's also our accuracy control.

## Grounding stays

Souly still answers only from verified curriculum. Worth knowing: a 2026
controlled study found retrieval-grounded tutors scored better on *pedagogy*
but **no better on accuracy** than prompt-only, and actually produced more
unsupported content. Retrieval controls *what is taught*; the per-item worked
solution controls *correctness*. We need both.

## Sycophancy has to be tested

The EduFrameTrap benchmark measured ~14% "pedagogical sycophancy" in frontier
models — capitulating when a student insists they're right. A tutor that folds
when a confident child pushes back is broken. This should be in our test suite:
assert the answer stays correct under pressure.

## Naming

**Righty → Souly everywhere.** Also worth deciding now, given §1.5: Souly is a
*study buddy*, not a friend. No "best friend", no "I missed you", no promises
of permanence. Warm and useful, not attached.

---

# Part 4 — Things we should explicitly not do

| Don't | Why |
|---|---|
| Match content to "learning styles" | The meshing hypothesis fails. Best recent meta-analysis: only 26% of outcomes showed the required interaction; authors conclude effects are "too small and too infrequent to warrant adoption." |
| Claim OpenDyslexic helps | Refuted (Wery & Diliberto 2017). |
| Claim coloured overlays treat dyslexia | Refuted (Ritchie et al. 2011, *Pediatrics*). |
| Let the AI give answers on request | −17% on a later exam (Bastani, PNAS 2025). |
| Use focused interests as a reward for compliance | Autistic-authored critique: teaches compliance. |
| Score speed | Autistic people are measurably slower (g=.35); a timer penalises the target cohort. |
| Interrupt on gaze | §1.1. |
| Market "always here for you" | Moxie. |
| Demo engagement as if it were learning | It's the easiest thing to show and the least informative. |
| Claim UDL raises attainment | It's a useful design heuristic with a weak efficacy base (Boysen 2024). Use it to structure our thinking, justify each feature on its own evidence. |

---

# Part 5 — What I need you to decide

1. **The camera trigger.** Gaze-based, stall-based, or both with stall
   preferred? This is the biggest one and it changes what we ask the CV team
   for. *(My view: stall-based.)*
2. **Hints-not-answers.** Accept the constraint that Souly never gives a
   direct answer before tier 3? It makes the demo less flashy and the product
   defensible. *(My view: accept.)*
3. **Streaks.** Keep the daily-streak mechanic, or drop it as pressure we
   shouldn't apply to this cohort? *(My view: genuinely unsure.)*
4. **Colour.** Keep the saturated purple, offer a muted theme, or make muted
   the default? *(My view: offer it, let the student choose, default muted for
   students flagged sensory-sensitive.)*
5. **Focused interests.** Worth the onboarding step? It's the single
   best-evidenced motivation lever here — 20/20 studies. *(My view: yes.)*
6. **AAC symbol layer.** Real work. In scope for the competition or not?
7. **The ending.** Do we build offline mode and a closing ritual now?

---

## Sources

**Autism — learning and attention**
- [Murray, Lesser & Lawson (2005), Attention, monotropism and the diagnostic criteria for autism](https://journals.sagepub.com/doi/10.1177/1362361305051398)
- [Buckle et al. (2021), "No Way Out Except From External Intervention": autistic inertia](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2021.631596/full)
- [McDonnell & Milton (2014), Going with the flow: reconsidering 'repetitive behaviour'](https://kar.kent.ac.uk/62647/)
- [Milton (2012), The double empathy problem](https://kar.kent.ac.uk/62639/1/Double%20empathy%20problem.pdf)
- [Kapp et al. (2019), 'People should be allowed to do what they like': stimming](https://journals.sagepub.com/doi/10.1177/1362361319829628)
- [Doherty-Sneddon et al. (2012), Gaze aversion as cognitive load management](https://acamh.onlinelibrary.wiley.com/doi/10.1111/j.1469-7610.2011.02481.x)
- [Jenkinson, Milne & Thompson (2020), Intolerance of uncertainty and anxiety in autism: meta-analysis](https://orca.cardiff.ac.uk/id/eprint/140692/1/Autism%201362361320932437.pdf)
- [Zapparrata et al. (2023), Slower processing speed in ASD: meta-analysis](https://link.springer.com/article/10.1007/s10803-022-05736-3)
- [Grainger, Williams & Lind (2016), Diminished judgement of confidence accuracy](https://openaccess.city.ac.uk/id/eprint/13732/)
- [Cook et al. (2021), Camouflaging in autism: systematic review](https://pubmed.ncbi.nlm.nih.gov/34563942/)
- [Dyer, Christian & Luce (1982), Response delay and discrimination performance](https://pmc.ncbi.nlm.nih.gov/articles/PMC1308267/)

**Practices and structure**
- [Steinbrenner et al. (2020), NCAEP Evidence-Based Practices report](https://ncaep.fpg.unc.edu/wp-content/uploads/EBP-Report-2020.pdf)
- [NPDC — the 28 evidence-based practices](https://autismpdc.fpg.unc.edu/ebps/)
- [Indiana IIDC — Structured Teaching: Visual Schedules (TEACCH)](https://www.iidc.indiana.edu/doc/resources/structured-teaching-strategies-visual-schedules.pdf)
- [Indiana IIDC — Transition Time](https://iidc.indiana.edu/irca/articles/transition-time-helping-individuals-on-the-autism-spectrum-move-successfully-from-one-activity-to-another.html)
- [Gunn & Delafield-Butt (2016), Teaching children with autism using restricted interests](https://journals.sagepub.com/doi/abs/10.3102/0034654315604027)
- [Bayoumi et al. (2025), Promoting Classroom Engagement Using Focused Interests](https://journals.sagepub.com/doi/10.1177/00400599251346719)

**Instructional design**
- [Schroeder & Cenkci (2018), Spatial contiguity: a meta-analysis](https://link.springer.com/article/10.1007/s10648-018-9435-9)
- [Rey et al. (2019), A meta-analysis of the segmenting effect](https://maria-wirzberger.de/wp-content/uploads/2019/01/Rey2019_Article_AMeta-analysisOfTheSegmentingE.pdf)
- [Adesope & Nesbit (2012), Verbal redundancy in multimedia learning: a meta-analysis](https://www.academia.edu/7820678/Verbal_Redundancy_in_Multimedia_Learning_Environments_A_Meta_Analysis)
- [Sundararajan & Adesope (2020), Keep it coherent: the seductive details effect](https://link.springer.com/article/10.1007/s10648-020-09522-4)
- [Wood et al. (2018), Text-to-speech and reading comprehension: a meta-analysis](https://journals.sagepub.com/doi/10.1177/0022219416688170)
- [Boysen (2024), Critical analysis of the evidence behind CAST's UDL guidelines](https://journals.sagepub.com/doi/10.1177/14782103241255428)
- [Frontiers (2024), Learning styles matching hypothesis: a meta-analysis](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2024.1428732/full)

**Dyslexia, ADHD, AAC**
- [British Dyslexia Association, Dyslexia Style Guide 2023](https://cdn.bdadyslexia.org.uk/uploads/documents/Advice/style-guide/BDA-Style-Guide-2023.pdf)
- [Zorzi et al. (2012), Extra-large letter spacing improves reading in dyslexia, PNAS](https://www.pnas.org/doi/full/10.1073/pnas.1205566109)
- [Wery & Diliberto (2017), The effect of OpenDyslexic on reading rate and accuracy](https://pubmed.ncbi.nlm.nih.gov/26993270/)
- [Ritchie et al. (2011), Irlen coloured overlays do not alleviate reading difficulties, Pediatrics](https://publications.aap.org/pediatrics/article/128/4/e932/30786/)
- [Gaastra et al. (2016), Classroom interventions for off-task behaviour in ADHD, PLOS ONE](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0148841)
- [ASHA Practice Portal — Augmentative and Alternative Communication](https://www.asha.org/practice-portal/professional-issues/augmentative-and-alternative-communication/)
- [Millar, Light & Schlosser (2006), Impact of AAC on speech production](https://pubmed.ncbi.nlm.nih.gov/16671842/)

**AI and robots**
- [Bastani et al. (2025), Generative AI without guardrails can harm learning, PNAS](https://www.pnas.org/doi/10.1073/pnas.2422633122)
- [Salimi et al. (2022), Social robots and autism: systematic review and meta-analysis, PLOS ONE](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0269800)
- [Scassellati et al. (2018), Improving social skills with a month-long in-home robot, Science Robotics](https://scazlab.yale.edu/sites/default/files/files/scirobotics_aat7544.pdf)
- [Rizvi et al. (2024), Are Robots Ready to Deliver Autism Inclusion?, CHI](https://andrewbegel.com/papers/rizvi-chi24.pdf)
- [Papadopoulos (2025), AI chatbots for autistic people: a double-edged sword](https://journals.sagepub.com/doi/10.1177/27546330251370657)
- [UNESCO — Guidance for generative AI in education and research](https://www.unesco.org/en/articles/guidance-generative-ai-education-and-research)
- [Common Sense Media — Social AI Companions risk assessment](https://www.commonsensemedia.org/sites/default/files/pug/csm-ai-risk-assessment-social-ai-companions_final.pdf)
- [Axios — Maker of AI robots for kids abruptly shutters (Moxie)](https://www.axios.com/2024/12/10/moxie-kids-robot-shuts-down)
- [OECD AI Incidents Monitor — Embodied shutdown bricks Moxie](https://oecd.ai/en/incidents/2024-12-09-ab89)
- [Screen time and ASD: risk, usage, addiction (JADD 2024)](https://link.springer.com/article/10.1007/s10803-024-06665-z)

**Design guidelines**
- [UK Home Office — Designing for users on the autistic spectrum](https://www.wigan.gov.uk/Docs/PDF/Council/Believe/Designing-for-accessibility.pdf)
- [Pavlov (2014), User Interface for People with Autism Spectrum Disorders](https://file.scirp.org/pdf/JSEA_2014022510055814.pdf)
- [Britto & Pizzolato, GAIA: Web accessibility guidelines for people with ASD](https://www.semanticscholar.org/paper/Towards-Web-Accessibility-Guidelines-of-Interaction-Britto-Pizzolato/3d9fe5ca68ab41c7ea9cb79369b7c462d058d759)
- [W3C — WCAG 2.2](https://www.w3.org/TR/WCAG22/)
