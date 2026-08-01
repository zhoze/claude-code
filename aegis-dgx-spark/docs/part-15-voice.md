# AEGIS Part 15 — Voice Interface (revised)

*Phase 9. Do not build this early — it is the largest surface area and the
least essential capability in the system.*

---

## Latency budget

The original said "low latency". That is not a target. This is:

| Stage | Budget |
|---|---:|
| Wake word detection | 200 ms |
| Speech-to-text (after speech ends) | 300 ms |
| Core planning + retrieval | 400 ms |
| First token from the model | 200 ms |
| Text-to-speech, first audio | 100 ms |
| **Wake word to first audio** | **1200 ms** |

Beyond about 1.5 s the interaction stops feeling like conversation. If the
budget cannot be met with the resident model, use a smaller model for voice
specifically and say so in the decision log — do not silently degrade.

Note the tension with Part 03: a large resident model and a tight latency
budget pull against each other. Measure before promising.

## Pipeline

```
mic → wake word (local, always on)
    → STT (local)
    → Core (classification: personal by default)
    → agents / models
    → TTS (local)
    → speaker
```

Every stage local. A voice assistant that ships audio to a cloud endpoint is
the exact product this project exists to replace.

## Privacy

- Audio is processed in memory and discarded. Storage is **off by default**.
- If you enable retention for debugging, it expires automatically and the fact
  that recording is on must be visible — an indicator, not a config file.
- The wake word can be disabled entirely, including by a physical mic switch.
  A hardware switch is worth more than any software guarantee.
- Transcripts follow Part 12 retention.

## Security

Sensitive actions require a second factor, because **voice is not
authentication**. Anyone in the room can speak, including through a window, a
phone speaker, or a television.

| Action class | Requirement |
|---|---|
| Query, retrieval | voice alone |
| Automations, notifications | voice alone |
| Sending mail, external release | confirmation on a second device |
| Locks, gates, alarms | second factor, always — never voice alone |

Speaker identification, if added later, is a convenience feature. It is not a
security control and must not be treated as one.

## Language

Estonian and English at minimum, likely Russian. Test wake-word false-positive
rates in the language you actually speak at home — models tuned on English
misfire in ways that are invisible until the system starts responding to your
dinner conversation.
