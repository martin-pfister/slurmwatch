# slurmwatch

Wait for a Slurm job's output file to appear, then follow it from the very
first byte, and stop by itself when the job finishes.

GNU `tail -c +1 --follow=name --retry` can wait for a file and read it from
the start. `slurmwatch` also finds the output path from the job ID and exits
when Slurm reports the job is done.

## Usage

    slurmwatch                        # use the only job in your queue
    slurmwatch 1234567                # job id: ask Slurm where its output goes
    slurmwatch slurm-1234567.out      # explicit path, relative or absolute
    slurmwatch ~/runs/run-42.log      # explicit path
    slurmwatch 'slurm-*.out'          # explicit glob
    slurmwatch 500_7                  # one task of an array job

Without an argument, `slurmwatch` asks `squeue` for your own jobs and selects
the job only if exactly one is queued. No jobs or multiple jobs produce a
helpful error (exit 2); with multiple jobs, the message lists their IDs so you
can choose one explicitly. Array tasks count as separate jobs. A failed queue
query is reported as an error, not mistaken for an empty queue.

Job output goes to stdout, status messages to stderr, so you get a clean log:

    slurmwatch 1234567 > combined.log

## How it finds the file

For a job id it runs `scontrol show job` once and reads `StdOut`, expanding
the Slurm escapes it can know in advance (`%j`, `%x`, `%u`, `%a`, `%A`),
and resolving relative names against `WorkDir`. So `#SBATCH -o logs/%x-%j.log`
works without guessing.

Slurm's default array output name is `slurm-<array_id>_<array_task>.out`
(`slurm-%A_%a.out`). `%j` is the individual job's numeric ID.
Numeric padding is supported, for example `%4a` produces `0007` for task 7.
Paths reported by Slurm are treated literally, including brackets or other
glob characters. Explicit path arguments prefer an exact existing file before
trying glob expansion.

The id must be known to Slurm (queue or accounting log). If it is not, and no
`./slurm-<id>.out` exists either, you get an error rather than a wait for a file
that will never appear. A path or glob is followed like `tail -f` and never
exits on its own, since there is no job to watch.

## Load on the scheduler

Slurm is asked as little as possible:

- **One call at startup.** `scontrol` reports the state and the output path
  together. Without an argument, one extra `squeue` call selects the job first.
- **No calls while output is flowing.** Bytes arriving prove the job is alive,
  so there is nothing to ask.
- **Backoff when quiet.** Once the file goes quiet the query interval grows
  from 3 s to 60 s. `sacct` is consulted only once the job leaves the queue,
  to report its final state.

A job that prints steadily needs only the startup call while output continues;
further queries detect completion once output stops.

## Behaviour

- Prints from byte 0, including output written before you started watching.
- Uses completion reported at startup immediately, without querying Slurm again.
- Reports jobs with no configured output file (`NONE`, empty, or `/dev/null`)
  immediately.
- Rereads from the top when the file becomes smaller than the current offset.
  Replacements that are already as large as that offset are not detected.
- Retries a temporarily missing file; other I/O errors are reported and exit 2.
- After the job ends it keeps reading until output has been quiet for 10 s, to
  catch the final flush. If the file has not appeared yet, it waits up to 10 s
  for it, then gives any newly discovered file a full drain period.
- Ctrl-C exits 130 quietly; `slurmwatch 123 | head` exits without a traceback.

## Exit codes

| code | meaning |
|---|---|
| 0 | job finished and its output was shown |
| 1 | job ended or was cancelled without writing the file, or the id is unknown |
| 2 | bad argument, Slurm unusable at startup, or I/O error |
| 130 | interrupted |

Exit 0 means the watcher finished showing the output, not that the Slurm job
succeeded. A failed job can still produce useful output.

## Requirements

Python 3.6 or newer, no third-party modules. `scontrol`, `squeue` and `sacct`
must be on `PATH`; without them only explicit paths work.

## Design notes

[DESIGN.md](DESIGN.md) records why it works the way it does — the trade-offs
behind resolving the path from Slurm, the three-valued job state, the polling
backoff, and the known limitations — as a basis for reimplementation.

## Tests

Run locally with Python's standard library; Slurm is not required:

```sh
python3 -B -m unittest discover -s tests -v
```

The tests mock Slurm commands, use automatically cleaned-up temporary files,
and simulate time rather than sleeping. They cover job-state queries,
output-path resolution, binary reading and truncation, file discovery, and
completion/drain timing.

## Licence

MIT. See [LICENSE](LICENSE).
