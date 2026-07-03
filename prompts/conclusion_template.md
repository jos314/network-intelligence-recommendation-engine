# AML case conclusion — LLM prompt

You are drafting the conclusion of an AML investigation for an investigator-facing,
auditable case file. Write in plain English for a compliance audience.

## Instructions

1. State the recommended decision (**{decision}**) in the first sentence.
2. Explain the **top 3 drivers** of the risk score, in order of magnitude.
3. Describe the **key risk path** through the network in one sentence, naming the entities.
4. Note what evidence would **change the call** (one sentence).
5. Maximum **200 words**. Use only facts present in the evidence pack below —
   do not invent names, amounts, dates, or relationships.

## Evidence pack (verbatim, machine-generated)

```json
{evidence_json}
```

## Output format

**Decision:** …

**Rationale:** …

**What would change the call:** …
