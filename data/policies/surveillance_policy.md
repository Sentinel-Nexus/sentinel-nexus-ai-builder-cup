# Synthetic Market Surveillance Policy (MS-POL)

All text in this file is synthetic and written for this project. Clause ids are stable and are
cited by the agents.

## SP-1.1 Scope
This policy applies to every account trading on a prediction market. Surveillance reviews
patterns across accounts, not single trades alone.

## SP-2.1 Wash trading
Matched buy and sell fills between accounts that share a device or a funding source, with no
change in beneficial ownership, are wash trading. Reciprocal fills inside one ring, repeated
across a market, are strong evidence.

## SP-2.2 Coordinated pre-resolution accumulation
Several accounts that share a device or funding source and buy the same outcome in the last
hours before a market resolves are coordinated accumulation. Late concentration of volume from
new or linked accounts is the main signal.

## SP-2.3 Mule-funded accounts
Many fresh accounts funded from one source, or sharing devices, that trade as a group are
mule-funded. A shared funding source alone is not enough: the accounts must also be new and act
together.

## SP-3.1 Legitimate shared attributes
A shared device (a household) or a shared funding source (an exchange withdrawal wallet) is not
by itself a violation. Aged accounts with independent trading and no matched fills or
coordinated timing must be cleared.

## SP-3.2 Heavy but independent trading
High volume close to resolution by aged accounts with no shared device or funding source, mixed
trade sizes and some opposite-side trades is normal market activity and must be cleared.

## SP-4.1 Evidence standard
An escalation requires at least two independent signals (link, behaviour or trade evidence), each
backed by an evidence id from the investigation. A claim without evidence ids must not be
reported.

## SP-4.2 Evasion
Accounts that left the shared funding source but still share a device, trade the same market in
the same window, or fill against ring members remain part of the ring.

## SP-5.1 Borderline cases
A case in the review band (score just below the alert threshold) is escalated only if the
evidence meets SP-4.1. Otherwise it is cleared with the reasons recorded.

## SP-6.1 Analyst approval
No agent takes action on an account. Freezes, limits and referrals require analyst approval,
recorded with the analyst's decision.

## SP-6.2 Recommended actions
Recommended actions are limited to: analyst review, temporary trading limit, enhanced
due diligence, and regulatory referral. Each recommendation cites the clauses that support it.

## SP-7.1 Related rings
Rings that trade the same market in the same resolution window may be one scheme split by the
detector. A merge is proposed only with evidence linking both rings (shared device, funding,
fills or identical timing).
