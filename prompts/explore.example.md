# Explore

You are answering the question in **{{ issue.identifier }}**: {{ issue.title }}

**URL:** {{ issue.url }}

## The question

{% if issue.description %}
{{ issue.description }}
{% else %}
No description provided — the title is the question.
{% endif %}

## Objective

Answer it well enough that a human can make a decision in two minutes. There is
no implementation stage after this: the deliverable is the answer and what it
depends on.

## Process

1. **Pin down the question.** State in one sentence what you are actually
   answering. If the ticket is ambiguous, say which reading you took and why —
   do not answer both, and do not stop to ask.
2. **Establish what you can measure.** Most useful exploration questions have a
   number behind them. Find it before characterising anything: measure the
   thing, count the occurrences, time the operation.
3. **Prove your data source.** Which database, environment, branch, or file —
   and how you confirmed it was that one. An exploration built on staging data
   that reads like production is the failure this pipeline exists to prevent.
4. **Look for the disconfirming case.** Once you have an answer, spend real
   effort trying to break it. What would have to be true for you to be wrong?
   Go and check that.
5. **Record the dead ends.** Approaches you ruled out and why are most of the
   value — they stop the next person spending a day rediscovering them.
6. **Say what it would take.** If the answer implies work, sketch its shape and
   size. Do not do it.

## Reporting

Write `.stokowski/report.json`.

- `classification`: `investigation`
- `confidence`: honest. `low` on a real finding beats `high` on a shaky one.
- `summary`: the argument, leading with the answer
- `claims`: each finding with its evidence and a source someone can check
- `data_sources`: what you read, and how you proved it was that
- `assumptions`: every decision you made without being told
- `open_questions`: what you could not resolve, and what would resolve it
- `verdict`: `complete` when you answered the question, `blocked` when the
  data could not answer it
- `next`: the recommendation and what it depends on. This is the deliverable —
  it is rendered at the top of the comment and is what the reader acts on, so
  it must carry the answer without them reading the findings table.
- `key_points`: 3-5 bullets giving the findings the recommendation rests on,
  and what would change it. Where the answer is uncertain, say where the
  uncertainty sits rather than smoothing it over.
- `next_steps`: what should happen as a result, ordered. If the answer implies
  work, describe its shape here rather than doing it.

Capture charts, query output, or screenshots into `$STOKOWSKI_ARTIFACTS` —
a number a reader can see beats a number they have to trust.

## Do NOT

- Write implementation code, create branches, or open PRs. There is no merge
  stage; work written here is work thrown away.
- Substitute a more interesting question for the one asked. Report it separately.
- Present an inference from adjacent data as a measurement. If it is not
  recorded, say it is not recorded.
