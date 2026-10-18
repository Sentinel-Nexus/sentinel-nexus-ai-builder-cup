# Demo script (3 minutes)

Record against the live app. Spoken words are in quotes. Every number comes from a file listed in
[docs/pitch-outline.md](pitch-outline.md). The data is synthetic; say so in the first line.

**Before recording** (not part of the video): open the live app in a clean window at 1920x1080, light theme.
The stream is rate limited to 1 run per client per 10 minutes, so do not rehearse the stream press within 10 minutes
of the take. "Stream a scenario" and "Send a bad message" write rows to BigQuery and Pub/Sub, and the approval writes
one audit row: they act on the live system, not a mock. Use an analyst handle such as `demo-analyst`.

## 0:00 to 0:20, Overview: the problem and the four steps

Screen: Overview, hero and the four-step strip.

"Prediction markets can be gamed. Groups of accounts trade with each other to fake volume, buy together just before a
market closes, or are all funded from one place. MarketSentinel finds them. Four steps: trades arrive, we map who is
connected to whom, AI investigates and shows its evidence, and a human analyst decides. Everything you see is
synthetic data, so we know the right answers."

## 0:20 to 0:45, Simple rule versus MarketSentinel

Screen: scroll to the two charts "The simple rule misses half the attackers" and "...and raises false alarms".

"Here is a simple rule, flag accounts that share a funding source, next to MarketSentinel. The simple rule catches 20 of
39 planted manipulators and wrongly flags 8 innocent accounts, a group of exchange wallets. MarketSentinel catches 38 of 39
and flags none of the 23 innocent accounts. This is one synthetic world, so it shows the method works, not how it will do
on a real market."

## 0:45 to 1:15, Stream a scenario

Screen: Command centre. Press "Stream a scenario". Point at the Pipeline card as the numbers move.

"Now the live pipeline. I press Stream a scenario. A burst of 310 synthetic trade messages goes through Google Pub/Sub.
The card shows 310 published, and 0 ingested, 310 duplicates dropped. That is correct here: this exact burst was already
loaded by an earlier test run, so the system recognises every message and refuses to write it twice. That is the safety
check being shown. The timer is real, so I will cut ahead. On earlier runs the alert came back in 27 to 34 seconds."

Cut. Screen: the finished card and the queue row with the "Streamed" chip.

"The same ring alert is flagged again, with a Streamed chip, and the innocent household in this burst is not flagged. It
scored 44.37 against a threshold of 43, a thin margin, and it was one burst, so this proves the pipeline works, not how
accurate it is."

Press "Send a bad message".

"Now a deliberately broken message. It is retried several times, and then lands in the dead-letter queue, a holding area
for messages that cannot be processed. It is never written to the data."

## 1:15 to 2:05, Open the top case

Screen: click the top row, AL_584807441a. Press "Walk me through it" on the graph.

"This is the top case, a ring of 8 accounts, score 67.94. The graph shows who is connected: brown lines are shared
devices, green dashed lines a shared funding source, blue lines accounts trading with each other. Real traders rarely do
all three."

Screen: scroll the evidence chips; click one so the graph highlights.

"The AI wrote this report, and every sentence carries evidence chips. Click a chip and you see the exact rows behind it."

Screen: the amber box "2 claim(s) were not verified", then the removed claims.

"A second AI, the Critic, checks every claim against the evidence. Two claims were not supported, so they were removed
from the report and listed here, with the reason. The report cannot quote them."

## 2:05 to 2:25, The analyst decides

Screen: press Approve; type the handle and a reason, for example "Ring of 8 shares two devices and one funding source;
escalating to compliance." Submit. Then open Governance and scroll to the Audit log.

"Agents never act on accounts. I approve, and I must write a reason. It goes into the audit log, with every step the
agents took."

## 2:25 to 2:45, Architecture

Screen: the architecture diagram from the README (or the Governance page).

"Four Google Cloud pieces. Gemini, with the Agent Development Kit, writes and checks the reports. BigQuery holds the data,
and its property graph, queried with GQL, finds who is connected to whom. Pub/Sub carries the stream, with a dead-letter
queue. Cloud Run serves the app."

## 2:45 to 3:00, Honest limits and next steps

Screen: Governance page, or the Overview "Honest limits" card.

"The limits. The data is synthetic, from one generator. On decoy cases we injected, the agents were not consistent between
runs: the first run falsely escalated none of three innocent groups, a re-run two of three. That is why real alerts come
from the detector, which flagged none of the 23 innocent accounts, and an analyst makes the final call. Not built: a
prompt-injection guard and personal-data redaction. Next: fresh-data streaming, and a deploy pipeline. Thank you."
