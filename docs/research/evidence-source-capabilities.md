# Evidence-source capabilities

**Scope.** This note answers what LeetCode, GitHub, and a Telegram-delivered
CV can reliably evidence for Wayfinder. It distinguishes observed platform
records from claims about competence. Sources were checked on 2026-08-05.

## Bottom line

Use these sources as consented, time-bounded corroboration. They can establish
that an account, repository, submission, or document exposed particular
metadata at collection time. None establishes independent authorship,
algorithmic understanding, time spent, or mastery. Any score or recommendation
must say that plainly and retain the raw-evidence provenance needed to correct
it.

## LeetCode

| Topic | What is dependable | Constraint / action |
| --- | --- | --- |
| Access | The present service takes only `LEETCODE_USERNAME`; it does not send LeetCode credentials. Its current GraphQL queries request recent accepted-submission `id`, `title`, `titleSlug`, and `timestamp`, then canonical title, difficulty, and topic tags. | This is an **undocumented GraphQL endpoint**, not a supported integration contract. The code executes it through Browserless because direct requests are expected to meet bot protection. Treat every response shape and availability characteristic as volatile; obtain account-holder consent for the username and browser-mediated collection. [Current client](../../src/leetcode_coach/integrations/leetcode.py) |
| Completeness and freshness | A successful fetch only says that the public endpoint returned those recent accepted submissions then. The current implementation caps the request at 20 and refreshes the pool weekly; it preserves solved state locally. | It is not a submission ledger. The client fetches neither timestamp nor submission ID, so the stored record cannot prove when an acceptance occurred or whether it is the first/only attempt. [Client](../../src/leetcode_coach/integrations/leetcode.py) |
| Failures / limits | Browserless misconfiguration, timeout, network errors, HTTP 429/5xx, 4xx, invalid JSON, GraphQL errors, and missing metadata are explicit failure paths. Transient failures retry three times. | Do not replace a failed fetch with an assumed solve history. There is no official LeetCode public API rate-limit or availability commitment cited here; record the fetch time and error, then ask the user to retry or provide another source. |
| Privacy / retention | A username and its returned activity are personal behavioural data once linked to a person. The request also goes through the configured Browserless service. | Define app-side retention/deletion before collecting. Do not claim LeetCode retention, privacy, authentication, or API stability guarantees that its accessible official interfaces do not publish for this endpoint. The site returned 403 to this research environment for its [Terms](https://leetcode.com/terms/) and [Privacy Policy](https://leetcode.com/privacy/), which is itself a reason not to infer permission from an undocumented endpoint. |

An accepted submission supports only: *this account exposed an accepted record
for this problem at collection time*. It does **not** show code quality,
complexity, independent work, reasoning, retries, or retained skill. Difficulty
and tags are problem metadata, not a measurement of the person's performance.

## GitHub

| Topic | What is dependable | Constraint / action |
| --- | --- | --- |
| Access and fields | Public REST data can be read unauthenticated. With the user's least-privilege token, REST/GraphQL can read private resources permitted by that token. Commits expose SHA, dates, message, author/committer data and signature-verification metadata; pull requests expose lifecycle, authors, changed files, additions/deletions, reviews, and merge state. The GraphQL contribution collection exposes calendar activity and grouped commit/PR/review/repository contributions. [REST authentication](https://docs.github.com/en/rest/authentication/authenticating-to-the-rest-api) [Commits API](https://docs.github.com/en/rest/commits/commits) [Pull requests API](https://docs.github.com/en/rest/pulls/pulls?apiVersion=latest) [Users GraphQL schema](https://docs.github.com/en/graphql/reference/users) | Authentication is required for private data and endpoint permissions can make inaccessible resources look like 403/404. Commit verification verifies a signature relationship, not that the account holder independently wrote the code. Store derived aggregates by default; do not retain private code, commit messages, email addresses, or diffs unless the product has a specific consented need. |
| Freshness and completeness | API polling captures the server's state at the response time. Webhooks can reduce polling delay. | There is no documented sync SLA. A webhook delivery that fails or takes more than 10 seconds is not automatically redelivered; manual redelivery covers only the previous three days. Reconcile by polling. Contribution graphs are also not complete ledgers: they have attribution and display eligibility rules. [Failed deliveries](https://docs.github.com/en/webhooks/using-webhooks/handling-failed-webhook-deliveries) [Webhook redelivery](https://docs.github.com/en/webhooks/testing-and-troubleshooting-webhooks/redelivering-webhooks) [Contribution criteria](https://docs.github.com/en/account-and-profile/reference/profile-contributions-reference) |
| Limits / failures | REST has a primary limit of 60 requests/hour unauthenticated and normally 5,000/hour authenticated. GraphQL normally has a 5,000-point/hour user limit; both APIs also enforce secondary limits. 403/429, `Retry-After`, reset headers, 5xx, timeout, partial GraphQL responses, and authorization errors must be handled. [REST limits](https://docs.github.com/en/rest/using-the-rest-api/rate-limits-for-the-rest-api?apiVersion=2022-11-28) [GraphQL limits](https://docs.github.com/en/graphql/overview/rate-limits-and-query-limits-for-the-graphql-api) | Back off and surface an incomplete-evidence state; never quietly substitute activity counts. Cache with collection timestamps and use conditional/paginated reads. |
| Privacy / retention | Public activity is visible by design. Reading private contributions or repositories materially expands the data set; private contribution display has special visibility rules. | Ask separately for private-repo access, use narrow scopes, and publish an app-side deletion/retention rule. GitHub documents only the three-day webhook-redelivery window above, not a general retention promise for data Wayfinder stores. [Profile privacy limits](https://docs.github.com/en/account-and-profile/reference/profile-reference) |

GitHub evidence establishes observable activity and artefacts, not ability. It
cannot validly prove the algorithmic correctness or complexity of a solution,
independent authorship, understanding, or performance outside the repositories
that were authorized and collected.

## CV delivered through Telegram

| Topic | What is dependable | Constraint / action |
| --- | --- | --- |
| Transport | Telegram updates can contain a `Document` with a `file_id`, filename, MIME type, and declared size. A bot can call `getFile`; its download URL is valid for at least one hour and standard Bot API downloads are limited to 20 MB. Bots can send documents of any type up to 50 MB. [Bot API: File / getFile](https://core.telegram.org/bots/api#getfile) [Bot API: sendDocument](https://core.telegram.org/bots/api#senddocument) | The existing service has no document/CV receive, download, malware scanning, text extraction, consent, or retention path. CV ingestion is therefore **not implemented** and must not be described as available. A user-provided MIME type, filename, declared size, and parsed text are untrusted input. |
| Evidence | A received document establishes only that the Telegram chat supplied bytes identified by Telegram at collection time. Parsed CV statements are self-asserted claims. | It cannot prove employer, role, education, dates, authorship, current employment, or algorithm mastery. Require explicit user confirmation of extracted fields and link claims to separate verifiable evidence where needed. |
| Privacy / retention | CVs commonly contain direct identifiers, contact details, employment and education history. Telegram's file transport does not define Wayfinder's retention. | Minimize collection, encrypt at rest if retained, restrict access, set deletion/expiry, and keep extraction output separate from the original. Do not log document bytes, file URLs, or extracted sensitive fields. |

## Recommended evidence policy

1. Record source, account/chat identifier, collection time, authorization mode,
   request scope, response version/hash, and fetch errors alongside each derived
   claim.
2. Treat every source as optional. Show missing, stale, inaccessible, or
   partial evidence as such; do not downgrade a person for a source they did
   not connect.
3. Use direct technical assessment (a consented problem attempt plus an
   explanation and follow-up questions) for algorithm mastery. Use LeetCode,
   GitHub, and CV data only to tailor that assessment and corroborate a narrow
   factual claim.
4. Before implementation, decide retention, deletion, consent withdrawal,
   token storage, private-repository scope, and an operational response for
   malware/oversize/parse failures. These are product and security decisions,
   not defaults supplied by the source APIs.
