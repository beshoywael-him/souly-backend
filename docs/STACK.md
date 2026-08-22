# Souly — Technology Stack (Source of Truth)

**Status:** Current as of 18 August 2026
**Supersedes:** the Spring Boot–era architecture doc, *and* the stack table in
`Souly Roadmap and project Phases.pdf`

> ### Two corrections to the roadmap PDF
>
> The roadmap PDF's stack table has the speech vendors the wrong way round.
> The correct assignment is below. If you are reading the PDF, ignore its
> TTS and STT rows.
>
> | | Roadmap PDF says | Actually decided |
> |---|---|---|
> | **STT** | Local Whisper *(pending confirm)* | **ElevenLabs** — confirmed |
> | **TTS** | ElevenLabs | **Not yet chosen** — see below |

---

## Stack

| Layer | Technology | Role | Status |
|---|---|---|---|
| **School CV** | Python + MediaPipe | Engagement/attention detection, publishes flag events | Existing code, pending review |
| **Backend** | FastAPI + SQLite | Flag lifecycle, mastery storage, WebSocket push, REST APIs | **Phase 1 built** |
| **LLM** | Gemini API (pay-as-you-go, Pro) | Agent reasoning, question generation, function/tool calling | Phase 2 |
| **RAG** | ChromaDB + sentence-transformers | Curriculum-grounded question generation | Phase 2 |
| **STT** | **ElevenLabs API** | Student's spoken answers → text | **Confirmed**, Phase 2 |
| **TTS** | **UNDECIDED** | Robot's spoken voice | **Open decision** |
| **Robot "hardware"** | Tablet or laptop, browser-based app, mounted inside 3D-printed shell | Mic (getUserMedia), speaker, touchscreen UI — no embedded/GPIO work | Phase 3–4 |
| **Teacher / Parent / Smart Screen** | HTML/JS + WebSocket | Same web stack as the robot UI — one frontend skillset covers all four screens | Phase 3 |
| **Network** | MiFi portable router | Single local network, all devices connect to it, zero venue Wi-Fi dependency | Needs load test |

### The unifying insight

With no Raspberry Pi, **all four "frontends" — teacher, parent, smart screen,
robot — are the same kind of artifact**: a web page, running in a browser, on
a device connected to the MiFi. The Interfaces squad isn't learning two
skillsets (web dev + embedded); it's building four web pages with different
content specs.

---

## The open TTS decision

The code is already structured so this decision costs nothing to defer and
nothing to change later. `TTS_PROVIDER`, `TTS_API_KEY`, `TTS_VOICE_ID`, and
`TTS_MODEL` are blank in `.env.example` and read through `app/config.py`.
**When the vendor is chosen, one file changes: `.env`.**

Some considerations for whoever makes the call:

| Option | For | Against |
|---|---|---|
| **ElevenLabs** | One vendor, one key, one bill — you're already using them for STT. Best-in-class expressiveness, which matters for an audience of children with communication differences. | Cost per character; another network round-trip at demo time. |
| **Google Cloud TTS** | Cheap, fast, very reliable. Same Google account as Gemini. | Voices are noticeably more robotic. |
| **Azure Neural TTS** | Strong accessibility tooling, good SSML control over pacing — useful for students who need slower speech. | A third vendor account to manage. |
| **Piper (local)** | Runs offline on the tablet. Zero latency, zero cost, immune to a MiFi hiccup mid-demo. | Setup effort; quality below the cloud options. |
| **Browser Web Speech API** | Free, zero integration. | Quality varies by browser/OS; least impressive in a judged demo. |

**The question worth asking before picking:** what happens to the robot's
voice if the MiFi drops mid-demo? A local option (Piper) is the only one that
keeps talking. If reliability outranks polish, that changes the answer.

---

## Phase status

| Phase | Scope | Status |
|---|---|---|
| **0 — Foundations** | Schema, config, seed data, stack doc | **Built** |
| **1 — Prove the spine** | `POST /flags` → DB → `GET /flags/pending` | **Built, 32 tests passing** |
| 2 — Brain + Voice | Gemini agent loop, RAG, ElevenLabs STT, TTS | Next |
| 3 — Interfaces + full wiring | Four screens, WebSocket push, full flag lifecycle | Pending |
| 4 — Full integration on real devices | Tablet in shell, all screens on MiFi | Pending |
| 5 — Demo hardening | Fallbacks, rehearsal, documentation PDF | Pending |

---

## Deferred to Phase 2 (deliberately not in the Phase 1 dependency list)

`google-generativeai`, `chromadb`, `sentence-transformers`, `elevenlabs`.

Phase 1 installs five packages and runs entirely offline. That is on purpose:
the spine must be demonstrable on a laptop with no internet and no API keys,
because at some point during this project that will be the situation you're in.
