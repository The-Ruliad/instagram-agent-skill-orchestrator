# TR_OS install

TR_OS keeps one reviewed copy of every third-party skill in
`~/TR_OS/skills/library/` and links it into the projects `skills/sets.json`
names. This folder installs the pack that way.

```bash
bash tros/install.sh                                   # website repo gets the set
bash tros/install.sh --project "$HOME/THE RULIAD/TR_Projects/SOME_CLIENT"
bash tros/install.sh --update                          # after pulling a reviewed change
```

What it does, in order:

1. **Stages** each skill folder byte-for-byte with `LICENSE` and a
   `SOURCE.txt` (`stage.sh`), then refuses to go on if any staged file is
   missing from `MANIFEST.sha256`, extra, or different.
2. **Configures TR_OS** (`configure.py`, idempotent, minimal diff):
   - an `instagram` set in `skills/sets.json` with all fifteen skills,
     linked into the website repo and every `--project`;
   - an `instagram_publish` deploy pattern in `~/.tr_os/permission_policy.json`,
     so any `igapi.py ... publish ... --confirm` waits on the operator like a
     production deploy. Dry runs, `--container-only`, reads and token
     refreshes stay ungated. Sessions pre-approve posts with
     `[grant:instagram_publish max=N]` under `## Approvals`.
3. **Imports and links** with `scripts/tros-skill-sync --import`.

New Claude sessions in those projects list `/ig-campaign` and `/seo-geo`;
running sessions keep what they started with.

## Changing a skill

The manifest is the review. After changing anything under `skills/`, read
every changed file, then:

```bash
bash tros/make-manifest.sh
python3 -m unittest discover -s tests
git add -A && git commit
```

and on the machine: `git pull && bash tros/install.sh --update`.
