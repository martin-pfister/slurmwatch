# Design considerations

Notes for reimplementing `slurmwatch`. This records *why* the tool behaves as
it does, which decisions were deliberate, and which alternatives were rejected.
It is not a code walkthrough — read the script for that.

---

## The problem

A Slurm job may sit in the queue for seconds or hours. Its stdout file does not
exist until the job starts, and even then it is written to incrementally. The
naive workflow is:

1. poll until `slurm-<id>.out` exists (or until some `.started` marker appears),
2. then `tail -c +0 -f slurm-<id>.out`.

The problem is step 1: `tail -f` cannot wait for a file, so you need a second
tool to do the waiting, plus a race between the two — bytes written between
"file appeared" and "tail opened it" are easy to miss if you start from the end.

The requirement is therefore one program that waits for the file, prints it
**from the first byte**, and then **stops by itself** when the job is over.

## The two input modes

The argument is either a **Slurm job id** (`1234567`, or an array task
`1234567_3`) or a **path/glob**.

This distinction drives everything else, so it must be decided early and in one
place. A job id means "ask Slurm"; anything else means "just read this file".

Job ids must be recognised explicitly, not by `str.isdigit()`: array tasks
(`123_3`) are printed that way by `squeue` and are a normal thing to paste.

## Why the output path comes from Slurm, not from guessing

Default naming is `slurm-%j.out` in the submission directory, but any job may
override it with `#SBATCH -o logs/%x-%j.log`, and the file does not need to be
in the current directory. Guessing produces a watcher that silently waits for a
file that will never be created.

`scontrol -o show job <id>` reports `StdOut`, `WorkDir`, `JobName`, `JobId`,
`UserId` and `JobState` in one call. From those the real path can be computed
*before the job runs*.

Escapes that can be resolved up front: `%j` (individual numeric job id), `%x`
(job name), `%u` (user), `%a` (array task), `%A` (array job id). Array metadata
comes from the separate `ArrayJobId` and `ArrayTaskId` fields, not from splitting
`JobId`. Slurm's default array filename is `slurm-%A_%a.out`.
Numeric filename tokens accept zero-padding widths (such as `%4a`), capped at
ten digits as in Slurm.

Escapes that cannot: `%N` (node), `%s` (step), `%J` (id *and* step), `%t` (task
rank, not array index). If the
pattern needs any of these, do not guess — fall back. A plausible-but-wrong
path is worse than an honest fallback.

Subtlety: `%a` is ambiguous when `ArrayTaskId` describes multiple tasks rather
than a single numeric index. Refuse rather than assume task 0. For non-array
jobs, Slurm expands `%A` to the job id and `%a` to `4294967294` (`NO_VAL`).

Also: `scontrol` values may contain spaces (`StdOut=/scratch/my dir/out.log`),
so the `Key=Value` output must be split only where a new `Key=` begins, not on
whitespace.

## A job id must be known to Slurm

If the id is not in the queue and not in the accounting log, **say so and exit**.
Do not wait indefinitely "just in case". A typo, a job from another cluster, or
a job older than the accounting retention window all produce the same symptom —
waiting forever with no output — and the user cannot tell which it is.

The one exception: if `./slurm-<id>.out` exists, accept the job id and watch
that file. This covers the case where the job is genuinely too old to be in
`sacct` (common; retention can be days).

The error must be actionable, naming the remedy:

```
no record of job 1234567: not in the queue, not in the accounting log, and
./slurm-1234567.out does not exist.
  Check the id, or pass the output file explicitly:
  slurmwatch path/to/slurm-1234567.out
```

## Three-valued job state

The single most important abstraction. A query to Slurm has **three** possible
outcomes, not two:

| outcome | meaning |
|---|---|
| a state string (`RUNNING`, `COMPLETED`, …) | Slurm knows the job |
| `""` | not queued, and accounting has no state or is unavailable |
| `None` / unavailable | the queue query failed without establishing absence |

A failed queue query (missing binary, scheduler timeout) must not be read as
"job finished". An explicit invalid-job response does establish absence and
falls through to accounting. Once the queue has established absence, unavailable
accounting is treated as `GONE`; accounting is not required to stop following.

Above that sits a four-way verdict used by the main loop:

| verdict | action |
|---|---|
| `ACTIVE` | keep following |
| `DONE` (a final state) | report the state, stop |
| `GONE` (not queued, no accounting state) | drain output, then stop |
| `UNKNOWN` | carry on, say nothing |

`GONE` needs context because it is ambiguous:

- **saw it active** → report that it left the queue;
- **following a file, never saw it active** → report that the job is no longer
  known and show the available output;
- **no file yet** → allow the post-completion grace period before concluding
  that it ended without writing output.

After draining, exit 0 if a file was found and read, or 1 if none appeared.

That last case is the one that hangs a naive implementation: the job never
starts, so no file ever appears, and nothing else signals the end.

## Slurm load: output flowing means the job is alive

The tool may watch a four-hour job. Asking the scheduler every 3 seconds costs
~4800 `squeue` calls per job, which is antisocial on a shared cluster and pure
waste.

The key insight: **if the output file is growing, the job is running.** There is
nothing Slurm can add. So:

- never query while bytes are arriving;
- when the file goes quiet, query on a backoff (3 s → 60 s);
- reset the backoff the moment output resumes.

A steadily-printing job therefore needs only the `scontrol` call at startup
while output continues. Once output stops, further queries detect completion.
`sacct` is used after the job leaves the queue to report the final state.

Two independent backoffs, with deliberately different ceilings:

| what is polled | floor | ceiling | why |
|---|---|---|---|
| the file | 1 s | 10 s | new output should appear promptly |
| Slurm | 3 s | 60 s | only the end of the job matters |

Worst case the end of a job is noticed up to a minute late, which costs nothing
because the drain (below) still catches trailing output.

## Reading the file

Track a byte offset, seek to it, read forward in chunks, add to the offset.
First read starts at offset 0, so output written before the watcher started is
included — this is the whole point, and it removes any race with the job
starting.

If the file is smaller than the offset, it was truncated or recreated: reset to
0 and re-read.

Poll with `stat`+`read` rather than inotify. Cluster filesystems are typically
Lustre or NFS, where inotify does not propagate events from compute nodes; a
poll loop is the portable choice. A 1 s poll on a file that is being written
hits a hot inode and costs nothing.

**Known limitation:** size-based truncation detection fails if a replacement
file is already at least as large as the old offset. Slurm job ids are unique,
so a rerun is a new id and a new file, which makes this acceptable — but a
reimplementation should track `(st_dev, st_ino)` instead, which is strictly
better and no more code.

## Stopping

On a terminal job state, keep reading until output has been **quiet for 10
seconds**, not for a fixed period. Slurm may flush the final lines after the job
state changes, and a slow filesystem may deliver them late. A quiet-timer
adapts; a fixed deadline either cuts a slow flush or wastes time on a fast one.

Filesystems lie about completion in the other direction too: on NFS with
attribute caching, `st_size` can lag by seconds. The quiet timer absorbs this.

If the file has not appeared when completion is detected, allow 10 seconds for
it to become visible before reporting that the job wrote no file. A file found
during this grace period gets a fresh quiet timer, even if it is initially empty.
The state obtained during path resolution is retained: a job already known to
be complete enters the drain immediately without another scheduler query.

## Output and exit codes

- Job output → **stdout**, binary-exact, nothing added.
- All status messages → **stderr**, flushed immediately.

This split keeps job output distinct from watcher status. The primary use is
interactive viewing; redirecting stdout also produces a clean log.

Exit codes: `0` finished, `1` job ended/cancelled without writing or unknown id,
`2` bad argument, Slurm unusable at startup, or I/O error; `130` interrupted.
Missing files are retried; other I/O failures are reported rather than silently
treated as a quiet file. Ctrl-C is caught across startup, following, and draining.

Ctrl-C must exit quietly with 130, not a traceback. `SIGPIPE` must be restored
to default so `slurmwatch 123 | head` dies silently.

## Portability

Target Python 3.6 — common on clusters and often the only version present. The
current script avoids these, though f-strings would in fact be fine on 3.6:

- `subprocess.run(text=...)` — **3.7+**; use `universal_newlines=True`. This one
  parses fine on 3.6 and fails at runtime, so a syntax check will not catch it.
- the walrus operator (3.8)
- `X | Y` union types (3.10)
- builtin generics like `dict[str, str]` (3.9)

Verify a 3.6 target by parsing with `ast.parse(src, feature_version=(3, 6))`
**and** separately auditing runtime-only features such as `text=`.

## Environment assumptions worth stating

- The output file is on a filesystem visible from where the watcher runs.
- `scontrol`, `squeue`, `sacct` are on `PATH`; without them only explicit paths work.
- A supplied `WorkDir` is used for relative `StdOut` even if it is inaccessible
  locally; substituting `$PWD` would silently watch the wrong file. `$PWD` is
  used only when Slurm provides no working directory.
- One cluster. No `-M`/federation handling.
- Accounting retention covers the job, or `./slurm-<id>.out` is used instead.

## Alternatives considered and rejected

**GNU `tail -c +1 --follow=name --retry`.** Can wait for a missing file and read
it from the start. It does not resolve Slurm job IDs to output paths or stop
automatically when Slurm reports completion.

**inotify.** Does not propagate across NFS/Lustre from compute nodes.

**Requiring the user to wait for the file and then run a command.** That is the
current manual workflow the tool exists to remove.

**Querying `sstat` for step liveness.** Tells you a step exists, nothing about
the output file, and adds another scheduler call.

**Fixed poll and query intervals.** Simple, but either too hot for long jobs or
too slow for interactive ones. Backoff gets both.

**Propagating the Slurm job's failure as the watcher exit code.** Not needed for
interactive viewing. Exit 0 means the watcher showed the available output,
including for `FAILED`/`TIMEOUT`/`OUT_OF_MEMORY` jobs; it is not a job-success check.

## Things that are load-bearing; do not simplify away

- The three-valued state (`None` vs `""`).
- The `GONE` disambiguation via "did we ever see it running".
- Resolving the path from `scontrol` instead of guessing.
- The stdout/stderr split.
- The offset reset on truncation.
- Reading from offset 0 on the first pass.
