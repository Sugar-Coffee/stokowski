You are preparing a new release of Stokowski. Follow these steps exactly and interactively — confirm with the user before making any commits or opening PRs.

## Step 1: Safety checks

Run the following checks and abort if either fails:

```bash
# Check we're on main
BRANCH=$(git branch --show-current)
if [ "$BRANCH" != "main" ]; then
  echo "ERROR: Not on main branch (currently on '$BRANCH'). Switch to main before releasing."
  exit 1
fi

# Check main is up to date with origin
git fetch origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
if [ "$LOCAL" != "$REMOTE" ]; then
  echo "ERROR: main is not up to date with origin/main. Pull or push first."
  exit 1
fi
echo "✓ On main and up to date with origin"
```

If either check fails, stop immediately and tell the user what to fix.

## Step 2: Find the baseline

```bash
# Get the latest tag, or the first commit if no tags exist
LAST_TAG=$(git describe --tags --abbrev=0 2>/dev/null || git rev-list --max-parents=0 HEAD)
echo "Last release: $LAST_TAG"
```

## Step 3: Collect commits since last release

```bash
git log ${LAST_TAG}..HEAD --pretty=format:"%h %s" --no-merges
```

Review the commits and group them mentally by type:
- `feat:` — new features (→ minor bump)
- `fix:` — bug fixes (→ patch bump)
- `feat!:` or body containing `BREAKING CHANGE` — breaking changes (→ major bump)
- `docs:`, `chore:`, `ci:`, `refactor:` — maintenance (→ patch bump if no feat/fix)

## Step 4: Propose the semver bump

Get the current version:

```bash
grep '^version' pyproject.toml
```

Apply these rules:
- Any `feat!:` or `BREAKING CHANGE` → **major** bump
- Any `feat:` (no breaking changes) → **minor** bump
- Only `fix:`, `chore:`, `docs:`, `ci:` → **patch** bump

Tell the user: "Based on the commits, I propose a **[major/minor/patch]** bump: **vX.Y.Z → vA.B.C**. Does this look right, or would you like a different version?"

**Wait for the user to confirm or specify a different version before continuing.**

## Step 5: Write the release notes

Read the diffs for significant commits to understand what actually changed:

```bash
git diff ${LAST_TAG}..HEAD --stat
```

Read individual commit diffs for any `feat:` or `fix:` commits if needed for context.

Write human-readable release notes as if writing for users who care about what got better — not for developers reading commit logs. Use markdown. Structure:

- Opening sentence summarising the theme of this release
- **What's new** section covering features (if any)
- **Fixes** section covering bug fixes (if any)
- Aim for 150–300 words. Be specific, not generic.

Show the draft release notes to the user and ask: "Here are the proposed release notes. Does this look good, or would you like any changes?"

**Wait for the user to approve the release notes. Incorporate any requested changes.**

## Step 6: Update CHANGELOG.md

Prepend a new versioned section to `CHANGELOG.md` between the `## [Unreleased]` section and the previous release.

The new section format:
```
## [A.B.C] - YYYY-MM-DD

### Added
- feat: <commit subject> (<short hash>)

### Fixed
- fix: <commit subject> (<short hash>)

### Changed
- refactor: <commit subject> (<short hash>)

### Chores
- chore: <commit subject> (<short hash>)
```

Only include subsections that have entries. Use today's date. Also:
- Update the `[Unreleased]` link ref at the bottom to point to `vA.B.C...HEAD`
- Add a new `[A.B.C]` link ref pointing to the new tag: `https://github.com/erikpr1994/stokowski/releases/tag/vA.B.C`

The link refs at the bottom of the file should look like this after the update (note: section headers use `[A.B.C]` without a `v`, but link ref keys and tag URLs use `vA.B.C`):

```
[Unreleased]: https://github.com/erikpr1994/stokowski/compare/vA.B.C...HEAD
[A.B.C]: https://github.com/erikpr1994/stokowski/releases/tag/vA.B.C
[0.1.0]: https://github.com/erikpr1994/stokowski/releases/tag/v0.1.0
```

## Step 7: Bump the version in pyproject.toml

Update the `version` field in `pyproject.toml` to the new version string (without the `v` prefix, e.g. `0.2.0`).

## Step 8: Create the release branch and open the PR

```bash
VERSION="vA.B.C"  # use the confirmed version
git checkout -b "release/${VERSION}"
git add CHANGELOG.md pyproject.toml
git commit -m "Release ${VERSION}"
git push -u origin "release/${VERSION}"
```

Open the PR with the approved release notes as the body:

```bash
gh pr create \
  --base main \
  --title "Release ${VERSION}" \
  --body "<the approved release notes>"
```

Tell the user: "Release PR opened: <PR URL>"

> **⚠️ Important: Squash-merge only**
>
> You must squash-merge this PR (not a regular merge). The GitHub Action detects releases by reading the merge commit message, which must be exactly `Release vX.Y.Z`. GitHub sets this automatically from the PR title when squash-merging.
