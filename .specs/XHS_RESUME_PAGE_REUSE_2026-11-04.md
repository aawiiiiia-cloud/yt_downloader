# XHS recovery and current-page reuse

Date: 2026-11-04. Scope: Electron collector and Python source runtime.

## Transfer policy

- Retain yt-dlp's HTTP transport, request context, cookies and proxy handling.
- Originals use identity encoding. In-task recovery requires a known full size,
  a strong quoted ETag and the same requested and final URL.
- Accept a continuation only with HTTP 206, the exact next byte offset,
  matching range total and suffix length, identical ETag, MIME and encoding.
- A range ignored with HTTP 200 restarts the file. Rejected 206 responses are
  discarded before a full GET. A 416/error is never treated as completion.
- At most two same-URL recoveries per opened original. This is not persistent
  resume across application restarts, nor continuation across CDN hosts.
- A response-generation change truncates the partial file and resets progress
  before writing new bytes. Keep decoding, validation and atomic publication.
- Large originals (at least 8 MiB, remaining ETA at least 32 seconds) can sample
  at most two alternate originals. Each sample is 64 KiB with a 1-second socket
  timeout and a nominal 3-second probe budget. An in-flight read can overrun the
  nominal budget; it is not an absolute wall-clock deadline.
- Keep the primary connection during probing. Choose another original only
  when its estimated full restart saves at least 3 seconds and 20 percent.
  Adopt its sampled connection/prefix, rather than issuing another GET.
- This short sample is a heuristic, not a guaranteed long-run rate. Deliberate
  sampling pauses do not count toward low-rate switching.
- Reuse successful exact-original preview/header observations for 60 seconds,
  up to 200 entries. They establish reachability, not throughput. Task-local
  measured source health still takes priority. Do not promote transformed,
  signed-query or unrelated image URLs into originals.
- Put the existing bd original node after i11/i10 for source diversity. No
  claim is made that one host is universally fastest.
- Keep the existing 96 KiB/s low-rate policy, 3-second startup grace, two
  4-second slow windows, 8-second near-completion protection and 15-second
  original idle timeout. Retry working slow originals before display fallback.

## Page reuse audit

| Site | Behavior |
| --- | --- |
| XHS images | Read the current matching note with a shared 1.2-second grace; use the link parser only when its data is unavailable. |
| XHS videos | Browser data indicates video presence; full video extraction still uses yt-dlp. |
| Bilibili | Use matching loaded playinfo first; missing data falls back after 1.2 seconds instead of an 8-second page-read failure. |
| Behance | Use the matching current project first; a missing snapshot gets a 1.2-second grace before the existing extractor fallback. |
| Direct Vimeo | Reuse the current root player or matching iframe config within 1.2 seconds. Freeze media-only private context and verify video/access-hash identity. Expired direct contexts fall back to normal extraction. |
| Behance external Vimeo | Keep explicit opt-in, source/module binding, private config and bounded refresh. Preparation/config reads share the deadline. |
| Taobao/Tmall | Keep browser-only product isolation and readiness checks; nested reads share the existing 4.5-second budget. |
| ZCOOL | Keep browser-only work isolation/readiness; nested reads share the existing 3.5-second budget. |
| Douyin | Already uses matching cache, visible page and a bounded same-session supplement; unchanged. |
| YouTube | Keep the managed login/PO-token/yt-dlp path; do not replace it with arbitrary browser responses. |

Cancellation, navigation checks, original image order, private signed URLs and
frozen queue contexts remain in place. No dependency update or new library.

## Validation

- JavaScript suite: 226 passed.
- Python discovery: 360 tests, 357 passed, 2 failed, 1 skipped.
  Both failures also reproduce against the unmodified production baseline:
  `test_both_python_associations_relay_to_bundle_before_legacy_imports` and
  `test_python_recovery_rejects_archive_path_traversal`. These concern the
  existing Windows launcher/PowerShell recovery environment; no launcher or
  security-policy changes are included here.
- Three new loopback HTTP tests use actual yt-dlp and final output bytes:
  valid 206 continuation, ignored range with a different equal-size body,
  and changed ETag rejection/full restart. All passed.
- Real Electron fixture smoke reached all modified parsing/download flows,
  including direct Vimeo through Python, private config and queue binding.
  The full run stopped at `xiaohongshu login opening did not finish`; the
  unmodified baseline stops at the same check. Later smoke checks therefore
  remain unverified in this run. Desktop screenshot capture also reported
  `UnknownVizError`; no claim of screenshot/visual validation is made.
- Syntax checks: all 9 changed Python and 7 changed JavaScript sources passed.

## Live sample

Anonymous original-image run for note `6a8fbc2400000000070063fc`:
parsing 3.115 seconds; downloading two images 12.334 seconds; total 15.449.

| Image | Bytes | Pixels | Download seconds | SHA-256 |
| --- | ---: | --- | ---: | --- |
| 1 | 15823624 | 2880 x 3840 | 7.027 | f927d6bf5f6ec7101006494259bd21c7ccfc74a89f17412a3969a3651f1e6a50 |
| 2 | 18363278 | 2880 x 3840 | 5.114 | 248960f93502f8aed1afa62671b4700e356e7f1b9f2369bf0a5fd0a943c606b6 |

Both files are byte-identical to the previously completed old-version originals.
No recovery or node probing was needed on this fast connection. This single
run does not prove universal speedup or live-CDN recovery; the interrupted
transfer cases are covered by deterministic HTTP tests instead.

Deploy only the scoped modified sources, tests and this report, after verifying
baseline hashes and backing up replaced files. Do not deploy copied distribution
tools, browser profiles, cached artifacts, live media, cookies or test runners.
