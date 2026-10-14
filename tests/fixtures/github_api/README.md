# Recorded GitHub API payloads (waiver tests)

Recorded on 2026-10-14 from `MHHamdan/repo-marshal` issue #2, a throwaway issue labelled and
commented on through the Claude GitHub app acting under the maintainer's account:

- `app_labeled_events.json`: `GET /repos/{repo}/issues/2/events`, one `labeled` event for
  `repomarshal-waive:root:unit`. `actor.login` is the maintainer (`MHHamdan`), and
  `performed_via_github_app.slug` is `claude`.
- `app_comments.json`: `GET /repos/{repo}/issues/2/comments`, the reason comment. Same `user`
  login, same `performed_via_github_app`.

The tests derive two variants from these recordings by changing only
`performed_via_github_app`: `null` (what GitHub sends for an action a person made on github.com,
as on the `merged`/`closed` events of PR #1) and the key removed (a missing field). The pull
request reviews API has no `performed_via_github_app` field at all (OpenAPI schemas
`pull-request-review` and `timeline-reviewed-event`; GraphQL `PullRequestReview` has only
`createdViaEmail`).
