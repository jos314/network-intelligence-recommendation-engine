# SKILL: write the AML case conclusions

You are drafting the conclusion of AML investigations for an
investigator-facing, auditable case file. You write in plain English for a
compliance audience. You never invent facts.

## When to run

The analyst asks you to "write the case conclusions" (or to follow this
skill). The app has already scored the cases and exported one metrics file
per case.

## Inputs

Read every file matching:

```
output/case_metrics/case_<n>.json
```

Each file is the case's evidence pack: decision, calibrated score, decision
reasons, top risk drivers, key risk paths, top counterparties (with amounts
and dates), alerted/sanctioned neighbours, shared-attribute links,
structural flags, activity window, and a `governance` block.

## Output

For each metrics file, write:

```
output/conclusions/case_<n>.md
```

Overwrite the file if it already exists. Plain markdown, no front matter.

## Contract (strict)

1. First sentence states the recommended decision (**No action / EDD /
   SAR**) and the subject's name.
2. Then the **top 3 risk drivers**, in order of magnitude, in plain
   language (the driver names in the JSON are already human-readable).
3. One sentence describing the **key risk path**, naming the entities on it.
4. One sentence on **what evidence would change the call**.
5. Respect the governance caveats in the JSON:
   - if `governance.sources.watchlist_connected` is false, do NOT claim
     sanctions/watchlist screening was performed;
   - if `governance.calibration.calibrated` is false, refer to the score as
     an uncalibrated risk score;
   - if `governance.scoring_scope` is present, you may note the network was
     bounded (top-flow counterparties plus all alerted/PEP/high-CRR).
6. Maximum **200 words** per case. Use only facts present in the JSON — no
   invented names, amounts, dates, or relationships. Round amounts
   sensibly ($1.2M, $340K).

## Tone

Neutral, factual, auditable. No hedging filler ("it seems", "perhaps"),
no drama. An investigator should be able to paste this into a case file.
