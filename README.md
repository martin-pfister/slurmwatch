# slurmwatch

Wait for a Slurm job's output file to appear, then follow it from the very
first byte, and stop by itself when the job finishes.

`tail -f` can't do this: it needs the file to exist already, so you end up
polling for `slurm.out.started` or re-running `tail` until the job begins.
`slurmwatch` waits for you, prints everything from the top of the file, and
exits when Slurm reports the job is done.

## Usage

    slurmwatch 1234567                # job id: ask Slurm where its output goes
    slurmwatch slurm-1234567.out      # explicit path, relative or absolute
    slurmwatch ~/runs/run-42.log      # explicit path
    slurmwatch 'slurm-*.out'          # explicit glob
    slurmwatch 500_7                  # one task of an array job

Job output goes to stdout, status messages to stderr, so you get a clean log:

    slurmwatch 1234567 > combined.log

## How it finds the file

For a job id it runs `scontrol show job` once and reads `StdOut`, expanding
the Slurm escapes it can know in advance (`%j`, `%x`, `%u`, `%a`, `%A`),
and resolving relative names against `WorkDir`. So `#SBATCH -o logs/%x-%j.log`
works without guessing.

Slurm's default array output name is `slurm-<array_id>_<array_task>.out`
(`slurm-%A_%a.out`). `%j` is the individual job's numeric ID.

The id must be known to Slurm (queue or accounting log). If it is not, and no
`./slurm-<id>.out` exists either, you get an error rather than a wait for a file
that will never appear. A path or glob is followed like `tail -f` and never
exits on its own, since there is no job to watch.

## Load on the scheduler

Slurm is asked as little as possible:

- **One call at startup.** `scontrol` reports the state and the output path
  together.
- **No calls while output is flowing.** Bytes arriving prove the job is alive,
  so there is nothing to ask.
- **Backoff when quiet.** Once the file goes quiet the query interval grows
  from 3 s to 60 s. `sacct` is consulted only once the job leaves the queue,
  to report its final state.

A four-hour job that prints steadily costs a single Slurm call in total.

## Behaviour

- Prints from byte 0, including output written before you started watching.
- Detects a truncated or recreated file and rereads it from the top.
- After the job ends it keeps reading until output has been quiet for 10 s, to
  catch the final flush. If the file has not appeared yet, it waits up to 10 s
  for it, then gives any newly discovered file a full drain period.
- Ctrl-C exits 130 quietly; `slurmwatch 123 | head` exits without a traceback.

## Exit codes

| code | meaning |
|---|---|
| 0 | job finished and its output was shown |
| 1 | job ended or was cancelled without writing the file, or the id is unknown |
| 2 | bad argument: missing directory, a directory instead of a file, or Slurm unusable |
| 130 | interrupted |

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
